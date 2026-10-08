import { MotionConfig } from "motion/react";

import { BlurFade } from "@/components/magicui/blur-fade";
import { BorderBeam } from "@/components/magicui/border-beam";
import { NumberTicker } from "@/components/magicui/number-ticker";

export interface IslandStat {
  id: string;
  label: string;
  value: number;
  start?: number;
}

export interface IslandProps {
  stats: IslandStat[];
  active: boolean;
  reduced: boolean;
}

export function MagicIsland({ stats, active, reduced }: IslandProps) {
  return (
    <MotionConfig reducedMotion="user">
      <div className="prime-ai-island-strip">
        {stats.map((stat, index) => (
          <div key={stat.id} className="prime-ai-island-tile" role="group" aria-label={`${stat.label}: ${stat.value}`}>
            <BlurFade delay={reduced ? 0 : index * 0.06} duration={reduced ? 0.01 : 0.4}>
              <span className="prime-ai-island-label">{stat.label}</span>
              <strong className="prime-ai-island-value" aria-hidden="true">
                {reduced ? (
                  <span>{stat.value}</span>
                ) : (
                  <NumberTicker
                    value={stat.value}
                    startValue={stat.start ?? 0}
                    style={{ color: "var(--text)" }}
                  />
                )}
              </strong>
            </BlurFade>
          </div>
        ))}
        {active && !reduced ? (
          <BorderBeam size={120} duration={9} colorFrom="var(--blue)" colorTo="var(--blurple)" borderWidth={1} />
        ) : null}
      </div>
    </MotionConfig>
  );
}
