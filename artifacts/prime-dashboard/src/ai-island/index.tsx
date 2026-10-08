import { createRoot, type Root } from "react-dom/client";

import css from "./island.css?inline";
import { MagicIsland, type IslandProps } from "./MagicIsland";

type MountProps = Omit<IslandProps, "reduced">;

const roots = new Map<HTMLElement, Root>();
const lastValues = new Map<string, number>();
let styleNode: HTMLStyleElement | null = null;

const prefersReduced = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function ensureStyle() {
  if (styleNode) return;
  styleNode = document.createElement("style");
  styleNode.dataset.primeAiIsland = "true";
  styleNode.textContent = css;
  document.head.append(styleNode);
}

function mount(host: HTMLElement, props: MountProps) {
  ensureStyle();
  const stats = props.stats.map((stat) => ({
    ...stat,
    start: lastValues.get(stat.id) ?? 0,
  }));
  props.stats.forEach((stat) => lastValues.set(stat.id, stat.value));
  const root = roots.get(host) ?? createRoot(host);
  roots.set(host, root);
  root.render(<MagicIsland stats={stats} active={props.active} reduced={prefersReduced()} />);
}

function dispose(host?: HTMLElement) {
  const targets = host ? [host] : Array.from(roots.keys());
  targets.forEach((node) => {
    roots.get(node)?.unmount();
    roots.delete(node);
  });
  if (!roots.size && styleNode) {
    styleNode.remove();
    styleNode = null;
  }
}

(window as unknown as { PrimeAIMagic: unknown }).PrimeAIMagic = { mount, dispose, version: 1 };
