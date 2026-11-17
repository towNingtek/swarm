// dsh-synapse launcher: a "🧠 策略中樞" link in the DSH Web client that opens /synapse/ in a new tab.
// Hand-written, no build step. Loaded by DSH as this package's ./client bundle (dsh.client in package.json);
// also served at /synapse/_/launcher.js.
//
// In the DSH client it is a row in the left sidebar, above 「設定」 (the `sidebar.footer.action` slot that
// DSH's own plugins use), styled like the 設定 row. It used to float at the bottom right, where it covered
// the composer's send button and model picker on narrow windows.
// Without the DSH module loader (the script loaded on its own) it still floats, as a small round button.
(function () {
	var URL_ = "/synapse/";
	var BUTTON_ID = "dsh-synapse-launcher";
	var LABEL = "策略中樞";
	var ICON = "🧠";
	var TITLE = "開啟策略中樞（新分頁）";

	// Fallback outside DSH: a small round icon button, label on hover.
	function mount() {
		if (typeof document === "undefined") return function () {};
		var old = document.getElementById(BUTTON_ID);
		if (old) old.remove();
		var a = document.createElement("a");
		a.id = BUTTON_ID;
		a.href = URL_;
		a.target = "_blank";
		a.rel = "noopener";
		a.title = TITLE;
		a.ariaLabel = LABEL; // the aria-label attribute (ARIA reflection)
		a.textContent = ICON;
		var s = a.style;
		s.position = "fixed";
		s.right = "12px";
		s.bottom = "64px";
		s.zIndex = "2147483000";
		s.width = "36px";
		s.height = "36px";
		s.display = "flex";
		s.alignItems = "center";
		s.justifyContent = "center";
		s.borderRadius = "50%";
		s.background = "#2a1f47";
		s.color = "#fff";
		s.font = "18px/1 system-ui, sans-serif";
		s.textDecoration = "none";
		s.boxShadow = "0 2px 8px rgba(0,0,0,.25)";
		s.opacity = "0.85";
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
			factory: function (require) {
				var module = { exports: {} };
				var exports = module.exports;
				Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
				// A sidebar row like DSH's 「設定」: icon only when the sidebar is a rail, icon + label when wide.
				function SidebarLink(props) {
					var rt = require("react/jsx-runtime");
					var wide = props && props.wide;
					return rt.jsxs("a", {
						id: BUTTON_ID,
						href: URL_,
						target: "_blank",
						rel: "noopener",
						title: TITLE,
						"aria-label": LABEL,
						"data-testid": "dsh-synapse-sidebar-link",
						style: {
							// DSH lays sidebar.footer.action entries out in a row (display:flex): take the whole row
							// (flex 1 0 100% + wrap on the parent) so the two launchers stack, one per line.
							boxSizing: "border-box", display: "flex", alignItems: "center", gap: 8,
							flex: "1 0 100%", height: wide ? 42 : 36, justifyContent: wide ? "flex-start" : "center",
							padding: wide ? "0 10px 0 8px" : 0, margin: wide ? "2px -2px" : "2px 0",
							borderRadius: "var(--dsw-radius-md, 8px)", color: "var(--dsw-alias-label-primary, inherit)",
							font: "inherit", fontSize: 14, lineHeight: "22px", textDecoration: "none", overflow: "hidden",
						},
						// The slot wrapper is display:contents; the row that lays entries out is the first real
						// box above it (DSH's footerActions). Let it wrap so each launcher gets its own line.
						ref: function (el) {
							var box = el && el.parentElement;
							while (box && getComputedStyle(box).display === "contents") box = box.parentElement;
							if (box) box.style.flexWrap = "wrap";
						},
						onMouseEnter: function (e) { e.currentTarget.style.background = "var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.12))"; },
						onMouseLeave: function (e) { e.currentTarget.style.background = "transparent"; },
						children: [
							rt.jsx("span", { "aria-hidden": "true", style: { width: 20, textAlign: "center", flex: "none" }, children: ICON }),
							wide ? rt.jsx("span", { style: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }, children: LABEL }) : null,
						],
					});
				}
				exports.apply = function (ctx) {
					if (ctx && ctx.slots && typeof ctx.slots.inject === "function") {
						ctx.slots.inject("sidebar.footer.action", function () {
							return ctx.slots.register({ name: "sidebar.footer.action", id: "dsh-synapse-launcher", order: 20 }, SidebarLink);
						});
						return;
					}
					// A DSH client without slots: fall back to the floating button.
					if (ctx && typeof ctx.effect === "function") ctx.effect(mount);
					else mount();
				};
				exports.inject = ["slots"];
				exports.URL = URL_;
				exports.SidebarLink = SidebarLink;
				return module.exports;
			}
		});
	} else {
		mount();
	}
})();
