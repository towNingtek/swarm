// dsh-swarm-board launcher: a "🌌 協作星系" link in the DSH Web client that opens /board/ in a new tab.
// Hand-written, no build step. Loaded by DSH as this package's ./client bundle (dsh.client in
// package.json); also served at /board/_/launcher.js, where it mounts directly when no DSH module
// loader is present.
//
// In the DSH client it is a row in the left sidebar, above 「設定」 (the `sidebar.footer.action` slot
// DSH's own plugins use), styled like the 設定 row. It used to float at the bottom right, where it
// covered the composer's send button and model picker on narrow windows. Without the DSH module
// loader it still floats, as a small round button.
(function () {
	var BOARD_URL = "/board/";
	var BUTTON_ID = "dsh-swarm-board-launcher";
	var SESSION_PARAM = "swarm-session";
	var LABEL = "協作星系";
	var ICON = "🌌";
	var TITLE = "開啟協作星系（新分頁）";

	function mount() {
		if (typeof document === "undefined") return function () {};
		var old = document.getElementById(BUTTON_ID);
		if (old) old.remove();
		var a = document.createElement("a");
		a.id = BUTTON_ID;
		a.href = BOARD_URL;
		a.target = "_blank";
		a.rel = "noopener";
		a.title = TITLE;
		a.ariaLabel = LABEL; // the aria-label attribute (ARIA reflection)
		a.textContent = ICON;
		var s = a.style;
		s.position = "fixed";
		s.right = "12px";
		s.bottom = "20px";
		s.zIndex = "2147483000";
		s.width = "36px";
		s.height = "36px";
		s.display = "flex";
		s.alignItems = "center";
		s.justifyContent = "center";
		s.borderRadius = "50%";
		s.background = "#1f2937";
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
			id: "dsh-swarm-board",
			factory: function (require) {
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
				// A sidebar row like DSH's 「設定」: icon only when the sidebar is a rail, icon + label when wide.
				function SidebarLink(props) {
					var rt = require("react/jsx-runtime");
					var wide = props && props.wide;
					return rt.jsxs("a", {
						id: BUTTON_ID,
						href: BOARD_URL,
						target: "_blank",
						rel: "noopener",
						title: TITLE,
						"aria-label": LABEL,
						"data-testid": "dsh-swarm-board-sidebar-link",
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
				function apply(ctx) {
					if (ctx && ctx.slots && typeof ctx.slots.inject === "function") {
						ctx.slots.inject("sidebar.footer.action", function () {
							return ctx.slots.register({ name: "sidebar.footer.action", id: "dsh-swarm-board-launcher", order: 21 }, SidebarLink);
						});
					} else if (ctx && typeof ctx.effect === "function") ctx.effect(mount);
					else mount();
					openFromUrl(ctx);
				}
				exports.apply = apply;
				exports.inject = ["uiWorkspace", "slots"];
				exports.SidebarLink = SidebarLink;
				exports.openFromUrl = openFromUrl;
				exports.BOARD_URL = BOARD_URL;
				return module.exports;
			}
		});
	} else {
		mount();
	}
})();
