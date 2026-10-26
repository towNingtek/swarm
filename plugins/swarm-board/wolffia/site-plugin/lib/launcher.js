// dsh-swarm-board launcher: a fixed "🌌 協作星系" button at the bottom right of the
// DSH Web client that opens /board/ in a new tab. Hand-written, no build step.
// Loaded by DSH as this package's ./client bundle (dsh.client in package.json);
// also served at /board/_/launcher.js, where it mounts directly when no DSH
// module loader is present.
(function () {
	var BOARD_URL = "/board/";
	var BUTTON_ID = "dsh-swarm-board-launcher";
	var SESSION_PARAM = "swarm-session";

	function mount() {
		if (typeof document === "undefined") return function () {};
		var old = document.getElementById(BUTTON_ID);
		if (old) old.remove();
		var a = document.createElement("a");
		a.id = BUTTON_ID;
		a.href = BOARD_URL;
		a.target = "_blank";
		a.rel = "noopener";
		a.title = "開啟協作星系（新分頁）";
		a.textContent = "🌌 協作星系";
		var s = a.style;
		s.position = "fixed";
		s.right = "16px";
		s.bottom = "16px";
		s.zIndex = "2147483000";
		s.padding = "8px 14px";
		s.borderRadius = "999px";
		s.background = "#1f2937";
		s.color = "#fff";
		s.font = "500 14px/1.2 system-ui, sans-serif";
		s.textDecoration = "none";
		s.boxShadow = "0 2px 8px rgba(0,0,0,.25)";
		s.opacity = "0.85";
		a.addEventListener("mouseenter", function () { s.opacity = "1"; });
		a.addEventListener("mouseleave", function () { s.opacity = "0.85"; });
		var attach = function () { document.body.appendChild(a); };
		if (document.body) attach();
		else document.addEventListener("DOMContentLoaded", attach, { once: true });
		return function () {
			document.removeEventListener("DOMContentLoaded", attach);
			a.remove();
		};
	}

	if (typeof window !== "undefined" && window.__ModuleLoader__) {
		window.__ModuleLoader__.load({
			id: "dsh-swarm-board",
			factory: function () {
				var module = { exports: {} };
				var exports = module.exports;
				Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
				// "在站台對話接著聊" on the board links to /?swarm-session=<id>: open that
				// Session through DSH's own workspace UI, then drop the parameter from the URL.
				function openFromUrl(ctx) {
					var id;
					try { id = new URL(window.location.href).searchParams.get(SESSION_PARAM); } catch (e) { return; }
					if (!id || !/^session-[0-9a-f-]{8,64}$/.test(id)) return;
					try {
						var u = new URL(window.location.href);
						u.searchParams.delete(SESSION_PARAM);
						window.history.replaceState(window.history.state, "", u.pathname + u.search + u.hash);
					} catch (e) {}
					var ws = ctx && ctx.uiWorkspace;
					if (!ws || typeof ws.openSession !== "function") return;
					// Startup restoration may select the last Session right after us; open again shortly after.
					var open = function () { try { ws.openSession(id); } catch (e) {} };
					open();
					[600, 2000].forEach(function (ms) { setTimeout(open, ms); });
				}
				function apply(ctx) {
					if (ctx && typeof ctx.effect === "function") ctx.effect(mount);
					else mount();
					openFromUrl(ctx);
				}
				exports.apply = apply;
				exports.inject = ["uiWorkspace"];
				exports.openFromUrl = openFromUrl;
				exports.BOARD_URL = BOARD_URL;
				return module.exports;
			}
		});
	} else {
		mount();
	}
})();
