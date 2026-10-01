"""Explicit in-process SIMULATED runners for isolated fixtures only.

These stand in for the trusted site executor and model verifier so a human can
walk the customer journey in a browser. They never touch Docker, DNS, nginx,
DSH, or any provider. Every result they produce is labelled simulated by the
underlying stores (`succeeded_simulated`, `simulated_pass`) and never yields
`ready_for_work` or `verified_for_work`.

Composition rules: the fixture creates the opaque capabilities and passes them
to the stores and to these runners in-process. No HTTP request can obtain a
capability; the customer probe route below only asks the runner to act on the
caller's own live session, and the store re-validates everything at publish.
"""
import threading

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from support_app import _error
from support_core import Conflict, InvalidInput, SupportError, Unauthorized
from support_model_settings import CustomerModelSettings
from support_site_jobs import SupportSiteJobs


class SimulatedModelVerifier:
    """Runs the store's simulated probe for the calling customer only."""

    def __init__(self, settings, capability, *, passing_selections=None):
        if type(settings) is not CustomerModelSettings or type(capability) is not object:
            raise InvalidInput('trusted settings and opaque capability required')
        self.settings = settings
        self._capability = capability
        # Deterministic outcome per catalog id; a fixture may mark some as failing
        # so the UI's failure path is exercised without any provider.
        self.passing = (set(settings.catalog) if passing_selections is None
                        else set(passing_selections) & set(settings.catalog))
        self._lock = threading.Lock()

    def probe(self, actor, expected_revision):
        with self._lock:
            ticket = self.settings.begin_simulated_probe(self._capability, actor, expected_revision)
            return self.settings.finish_simulated_probe(
                self._capability, ticket, passed=ticket.selection_id in self.passing)


class SimulatedSiteExecutor:
    """Completes queued jobs as `succeeded_simulated`; no external effects."""

    def __init__(self, jobs, capability):
        if type(jobs) is not SupportSiteJobs or type(capability) is not object:
            raise InvalidInput('trusted jobs store and opaque capability required')
        self.jobs = jobs
        self._capability = capability
        self._lock = threading.Lock()

    def run_once(self):
        """Claim and simulate every queued job; return the processed job ids."""
        done = []
        with self._lock:
            for job_id in self.jobs.queued_job_ids(self._capability):
                lease = self.jobs.claim(self._capability, job_id, lease_seconds=60)
                if lease is None:
                    continue
                self.jobs.complete_simulated(self._capability, lease)
                done.append(job_id)
        return done


def install_simulated_model_probe(app, verifier):
    """POST /customer/model-settings/simulated-probe {expected_revision:int}.

    Only installed by explicit fixture composition. The route cannot choose the
    outcome, the selection, or another principal: everything derives from the
    caller's live session and the store's revision/lifecycle fences.
    """
    if type(verifier) is not SimulatedModelVerifier:
        raise ValueError('simulated verifier required')
    if not callable(getattr(app.state, 'support_actor_for', None)):
        raise ValueError('customer session adapter required')
    if getattr(app.state, 'support_simulated_probe_installed', False):
        raise ValueError('simulated probe already installed')
    from support_chat_app import strict_body

    @app.post('/customer/model-settings/simulated-probe')
    async def simulated_probe(request: Request):
        try:
            actor = await app.state.support_actor_for(request)
            if request.scope.get('query_string'):
                raise InvalidInput('query not accepted')
            body = await strict_body(request, {'expected_revision': int})
            value = await run_in_threadpool(verifier.probe, actor, body['expected_revision'])
            return JSONResponse(value)
        except Conflict:
            return _error(409)
        except OverflowError:
            return _error(413)
        except (ValueError, InvalidInput, UnicodeError):
            return _error(400)
        except Unauthorized:
            return _error(401)
        except SupportError:
            return _error(401)

    app.state.support_simulated_probe_installed = True
    return app
