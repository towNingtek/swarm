window.__ModuleLoader__.load({
	id: "swarm-brand",
	factory: (require) => {
		// Swarm presentation layer for the DSH Web client. Pure presentation: no
		// network, no storage.
		//  - Brand occupants for the sidebar and blank-session hero slots
		//    (replaces @deepseek-ai/dsh-client-ui-brand-official, disabled in the
		//    bundle patch).
		//  - A 繁體中文 (zh-TW) language falling back to DSH's zh dictionaries,
		//    converted through a generated table (tools/build-zh-tw.mjs).
		//  - The hero tagline replaced with Swarm copy.
		// lib/client.js is GENERATED from src/client.js; edit the source.
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const jsx = require("react/jsx-runtime");
		const SHAPES = [["polygon", {"points": "11.7,14.4 18.5,18.4 18.5,26.1 11.7,30.1 5.0,26.1 5.0,18.3"}], ["polygon", {"points": "27.3,14.4 34.0,18.4 34.0,26.1 27.3,30.1 20.5,26.1 20.5,18.3"}], ["polygon", {"points": "19.5,27.9 26.3,31.9 26.3,39.6 19.5,43.5 12.7,39.6 12.7,31.8"}], ["circle", {"cx": "35.5", "cy": "12.5", "r": "3.0"}], ["circle", {"cx": "41.5", "cy": "6.0", "r": "2.2"}], ["circle", {"cx": "44.5", "cy": "15.5", "r": "1.6"}]];
		const ZH_TW = __ZH_TW__;
		const TAIWAN = "zh-TW";
		// Swarm copy replacing DSH texts, per locale; the zh-TW row wins in 繁中.
		const COPY = {
			"conversation": {
				"hero.headline": { "zh-tw": "今天想交代什麼工作？", "zh": "今天想交代什么工作？", "en": "What should the swarm work on?" },
				"hero.preview": { "zh-tw": "AI 辦公室", "zh": "AI 办公室", "en": "AI office" }
			}
		};
		function toTaiwan(text) {
			if (typeof text !== "string") return text;
			const exact = ZH_TW.EXACT[text];
			if (exact !== undefined) return exact;
			let out = "";
			for (const ch of text) out += ZH_TW.CHARS[ch] ?? ch;
			return out.replaceAll("DeepSeek Harness", "Swarm");
		}
		function SwarmMark({ size = 24, className }) {
			return jsx.jsx("svg", {
				width: size, height: size, className, viewBox: "0 0 48 48",
				fill: "currentColor", "aria-hidden": "true", focusable: "false",
				children: SHAPES.map(([tag, attrs], i) => jsx.jsx(tag, { ...attrs }, i))
			});
		}
		function SwarmName() {
			return jsx.jsx("span", {
				style: { fontWeight: 700, letterSpacing: "0.22em", fontSize: "14px" },
				children: "SWARM"
			});
		}
		// Layer Taiwan text and Swarm copy over the locale runtime's own lookup.
		// Its dictionaries, fallback chain and preference handling stay DSH's.
		function installLanguage(locale) {
			if (locale.__swarmLanguage) return;
			locale.__swarmLanguage = true;
			const lookup = locale.lookup.bind(locale);
			const resolveText = locale.resolveText.bind(locale);
			const taiwan = () => String(locale.getSnapshot().active).toLowerCase() === "zh-tw";
			const copy = (ns, key) => {
				const row = COPY[ns]?.[key];
				if (row === undefined) return undefined;
				const active = String(locale.getSnapshot().active).toLowerCase();
				return row[active] ?? row[active.split("-")[0]] ?? row.en;
			};
			locale.lookup = (ns, key, chain) => {
				const own = copy(ns, key);
				if (own !== undefined) return own;
				const value = lookup(ns, key, chain);
				return value !== undefined && taiwan() ? toTaiwan(value) : value;
			};
			locale.resolveText = (text) => {
				const value = resolveText(text);
				return taiwan() ? toTaiwan(value) : value;
			};
			locale.addLanguage({ id: TAIWAN, label: "繁體中文", fallback: "zh" });
		}
		const inject = ["slots", "locale"];
		function apply(ctx) {
			installLanguage(ctx.locale);
			ctx.slots.inject("sidebar.brand.mark", () => ctx.slots.inject("sidebar.brand.name", function* () {
				yield ctx.slots.register({ name: "sidebar.brand.mark" }, SwarmMark);
				yield ctx.slots.register({ name: "sidebar.brand.name" }, SwarmName);
			}));
			ctx.slots.inject("conversation.hero.brand.mark", () =>
				ctx.slots.register({ name: "conversation.hero.brand.mark" }, SwarmMark));
		}
		exports.apply = apply;
		exports.inject = inject;
		exports.toTaiwan = toTaiwan;
		return module.exports;
	}
});
