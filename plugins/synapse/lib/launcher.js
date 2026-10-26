// dsh-synapse launcher: a fixed "🧠 策略中樞" button in the DSH Web client that opens
// /synapse/ in a new tab. Sits just above the 協作星系 button. Hand-written, no build step.
(function () {
	var URL_ = "/synapse/";
	var BUTTON_ID = "dsh-synapse-launcher";

	function mount() {
		if (typeof document === "undefined") return function () {};
		var old = document.getElementById(BUTTON_ID);
		if (old) old.remove();
		var a = document.createElement("a");
		a.id = BUTTON_ID;
		a.href = URL_;
		a.target = "_blank";
		a.rel = "noopener";
		a.title = "開啟策略中樞（新分頁）";
		a.textContent = "🧠 策略中樞";
		var s = a.style;
		s.position = "fixed";
		s.right = "16px";
		s.bottom = "60px";
		s.zIndex = "2147483000";
		s.padding = "8px 14px";
		s.borderRadius = "999px";
		s.background = "#2a1f47";
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
			id: "dsh-synapse",
			factory: function () {
				var module = { exports: {} };
				var exports = module.exports;
				Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
				// Same shape as dsh-swarm-board: ctx.effect runs mount and keeps its return as cleanup.
				exports.apply = function (ctx) {
					if (ctx && typeof ctx.effect === "function") ctx.effect(mount);
					else mount();
				};
				exports.URL = URL_;
				return module.exports;
			}
		});
	} else {
		mount();
	}
})();
