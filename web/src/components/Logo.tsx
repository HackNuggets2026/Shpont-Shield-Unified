// Brand logos for services, resources and models, from the same pinned, permissively licensed icon sets the
// Shield console uses (jsDelivr): Simple Icons (CC0, one colour, drawn as a mask in the brand colour), devicon
// (MIT) and LobeHub icons (MIT) for model providers. Black-on-transparent marks are drawn in the text colour,
// so they stay visible in dark mode. Anything unknown gets a lettered tile.
import type { ReactNode } from "react";
import { IconInbox, IconServer } from "./icons";
import { cx } from "./ui";

type Src = { img: string } | { mask: string; color: string };

const SI = (slug: string, hex?: string): Src => ({
  mask: `https://cdn.jsdelivr.net/npm/simple-icons@16.33.0/icons/${slug}.svg`,
  color: hex ? `#${hex}` : "rgb(var(--ink))",
});
const DEV_MASK = (path: string, hex: string): Src => ({ mask: `https://cdn.jsdelivr.net/npm/devicon@2.17.0/icons/${path}.svg`, color: `#${hex}` });
const LOBE = (name: string): Src => ({ img: `https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.95.1/icons/${name}.svg` });
const LOBE_MONO = (name: string): Src => ({
  mask: `https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.95.1/icons/${name}.svg`,
  color: "rgb(var(--ink))",
});

const BRANDS: Record<string, [string, Src]> = {
  claude: ["Claude (Anthropic)", LOBE("claude-color")],
  openai: ["OpenAI", LOBE_MONO("openai")],
  meta: ["Llama (Meta)", LOBE("meta-color")],
  qwen: ["Qwen", LOBE("qwen-color")],
  ollama: ["Ollama", LOBE_MONO("ollama")],
  github: ["GitHub", SI("github")],
  githubactions: ["GitHub Actions", SI("githubactions", "2088FF")],
  aws: ["AWS", DEV_MASK("amazonwebservices/amazonwebservices-plain-wordmark", "FF9900")],
  apple: ["Apple", SI("apple")],
  postgres: ["PostgreSQL", SI("postgresql", "4169E1")],
  kubernetes: ["Kubernetes", SI("kubernetes", "326CE5")],
};

/** Catalog resource ids and other names that stand for a brand. */
const ALIASES: Record<string, string> = {
  claude_code: "claude",
  "gpt-4o": "openai",
  "gpt-4o-mini": "openai",
  llama: "meta",
  qwen: "qwen",
  ci_minutes: "githubactions",
  cloud: "aws",
  simulator: "apple",
  prod_db: "postgres",
  prod_deploy: "kubernetes",
};
const MODELS: [RegExp, string][] = [
  [/^claude/, "claude"],
  [/^(gpt|o\d|chatgpt|text-embedding)/, "openai"],
  [/^(llama|codellama)/, "meta"],
  [/^qwen/, "qwen"],
];
/** Things with no brand but an obvious picture. */
const GLYPHS: Record<string, [string, (size: number) => ReactNode]> = {
  vm: ["Sandbox VM", (s) => <IconServer size={s} />],
  external_email: ["Email", (s) => <IconInbox size={s} />],
};

export function brandOf(key: string | null | undefined): string | null {
  const raw = String(key ?? "").toLowerCase();
  if (!raw) return null;
  if (ALIASES[raw]) return ALIASES[raw];
  if (BRANDS[raw]) return raw;
  return MODELS.find(([re]) => re.test(raw))?.[1] ?? null;
}

/** A logo for a resource id, model name or brand; `label` is used for the lettered fallback and the tooltip. */
export function Logo({ id, label, size = 16, className }: { id: string | null | undefined; label?: string; size?: number; className?: string }) {
  const box = { width: size, height: size };
  const base = cx("inline-block shrink-0 align-[-3px]", className);
  const brand = brandOf(id);
  if (brand) {
    const [name, src] = BRANDS[brand];
    if ("img" in src) return <img src={src.img} alt={name} title={name} loading="lazy" className={cx(base, "object-contain")} style={box} />;
    return (
      <span
        role="img"
        aria-label={name}
        title={name}
        className={base}
        style={{
          ...box,
          background: src.color,
          WebkitMaskImage: `url(${src.mask})`,
          maskImage: `url(${src.mask})`,
          WebkitMaskSize: "contain",
          maskSize: "contain",
          WebkitMaskRepeat: "no-repeat",
          maskRepeat: "no-repeat",
          WebkitMaskPosition: "center",
          maskPosition: "center",
        }}
      />
    );
  }
  const glyph = GLYPHS[String(id ?? "")];
  if (glyph) {
    return (
      <span role="img" aria-label={glyph[0]} title={glyph[0]} className={cx(base, "inline-flex items-center justify-center text-ink2")} style={box}>
        {glyph[1](size)}
      </span>
    );
  }
  const text = label || String(id ?? "?");
  return (
    <span
      role="img"
      aria-label={text}
      title={text}
      className={cx(base, "inline-flex items-center justify-center rounded bg-ink/[0.08] font-bold leading-none text-muted")}
      style={{ ...box, fontSize: Math.round(size * 0.62) }}
    >
      {(text.replace(/[^a-z0-9]/gi, "")[0] || "?").toUpperCase()}
    </span>
  );
}
