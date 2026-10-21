"""Optional platform plugins: extra behaviour kept outside this repository.

``PLATFORM_PLUGINS`` lists plugin DIRECTORIES, comma separated::

    PLATFORM_PLUGINS=/opt/plugins/alpha,/opt/plugins/beta

Each directory holds ``plugin.py`` with ``install(context)``. Both services call
it once while they are being built:

* ``platform_service.build()`` with ``context.service == 'platform'``
* ``admin_service.build()``    with ``context.service == 'admin'``

``context`` is a plain namespace of the objects the service has built (see the
two call sites for the exact fields; a field the service does not have is
``None``). A plugin may add routes or middleware to the apps it is handed and
set the scheduler's hook attributes (``site_office``, ``site_runner``,
``tick_hooks``; see support_site_scheduler.py).

Plugins are trusted code chosen by the operator, never by a request or a site.
A plugin that cannot be found, does not import, or raises in ``install`` is
logged and skipped: the platform always starts without it. The plugin directory
is put on ``sys.path`` so ``plugin.py`` can import its own modules.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

ENV = 'PLATFORM_PLUGINS'
_NAME = re.compile(r'[^A-Za-z0-9_]')


def plugin_dirs(value):
    """``PLATFORM_PLUGINS`` -> list of directory paths (order kept, no duplicates)."""
    out = []
    for item in (value or '').split(','):
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


def _log(message):
    print(f'[plugins] {message}', flush=True)


def _load(directory, index):
    path = Path(directory)
    if not path.is_absolute():
        raise ValueError('plugin directory must be an absolute path')
    entry = path / 'plugin.py'
    if not entry.is_file():
        raise FileNotFoundError('plugin.py not found')
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    name = f'platform_plugin_{index}_{_NAME.sub("_", path.name)}'
    spec = importlib.util.spec_from_file_location(name, entry)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if not callable(getattr(module, 'install', None)):
        raise TypeError('plugin.py has no install(context)')
    return module


def install_plugins(service, value=None, **objects):
    """Install every plugin named by ``value`` (default: ``$PLATFORM_PLUGINS``).

    Returns the list of directories whose ``install`` completed. Never raises.
    """
    value = os.environ.get(ENV, '') if value is None else value
    installed = []
    for index, directory in enumerate(plugin_dirs(value)):
        context = SimpleNamespace(service=service, plugin_dir=directory, **objects)
        try:
            module = _load(directory, index)
            module.install(context)
        except Exception as exc:  # a broken plugin must never stop the platform
            _log(f'{directory}: skipped ({type(exc).__name__}: {str(exc)[:200]})')
            traceback.print_exc()
            continue
        installed.append(directory)
        _log(f'{directory}: installed ({service})')
    return installed
