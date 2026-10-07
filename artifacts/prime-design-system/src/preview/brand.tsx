import primeTeamAvatar from '../assets/prime-team-avatar.png';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card';
import { Guidelines } from './parts';

const sourceGuidance = [
  { kind: 'do', text: 'Use bundled DejaVu Sans for Arabic-capable UI text; keep Arabic runs directionally correct and legible at compact sizes.' },
  { kind: 'do', text: 'Use PRIME blue for primary emphasis, blurple for secondary platform cues, and cyan for high-salience progress.' },
  { kind: 'do', text: 'Reserve status colors for their semantic roles.' },
  { kind: 'do', text: 'Preserve the streak values, rank data, stage labels, and milestone thresholds supplied by the bot.' },
  { kind: 'dont', text: 'Do not invent thresholds, stage names, or stage artwork. Render stage imagery only when the bot already has a supplied asset.' },
  { kind: 'dont', text: 'Do not reuse the Discord reference screenshot’s member name or personal avatar as sample content in new UI.' },
] as const;

export function BrandPage() {
  return (
    <div className="space-y-6">
      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(260px,0.75fr)]">
        <Card className="overflow-hidden border-primary/30 bg-card">
          <CardHeader className="pb-3">
            <p className="text-xs font-bold uppercase tracking-widest text-primary">IDENTITY ASSET</p>
            <CardTitle className="text-2xl">PRIME TEAM</CardTitle>
            <CardDescription>The supplied avatar crop is the visual signature. It is an avatar, not a wordmark.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5 sm:flex-row sm:items-center">
            <div className="flex h-28 w-28 shrink-0 items-center justify-center rounded-2xl border border-border bg-muted p-2">
              <img
                src={primeTeamAvatar}
                alt="Exact supplied PRIME TEAM avatar crop"
                className="h-full w-full rounded-xl object-cover"
              />
            </div>
            <div className="space-y-3">
              <p className="text-sm leading-6 text-muted-foreground">
                Keep the original crop intact. Use it as the team identity image in this browser and related brand references; do not redraw it or treat it as a scalable logo.
              </p>
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="rounded-md border border-border bg-muted px-2.5 py-1.5 text-muted-foreground">Original asset</span>
                <span className="rounded-md border border-border bg-muted px-2.5 py-1.5 text-muted-foreground">Avatar only</span>
                <span className="rounded-md border border-border bg-muted px-2.5 py-1.5 text-muted-foreground">No redraw</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader>
            <p className="text-xs font-bold uppercase tracking-widest text-accent">PRODUCT CONTEXT</p>
            <CardTitle>One community, two surfaces</CardTitle>
            <CardDescription>Member engagement and community operations share one visual language.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-lg border border-border bg-muted p-3">
              <p className="text-sm font-semibold">Member experience</p>
              <p className="mt-1 text-sm text-muted-foreground">Streaks and engagement rewards, presented with clarity.</p>
            </div>
            <div className="rounded-lg border border-border bg-muted p-3">
              <p className="text-sm font-semibold">Companion dashboard</p>
              <p className="mt-1 text-sm text-muted-foreground">A focused workspace for administrators.</p>
            </div>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <p className="text-xs font-bold uppercase tracking-widest text-muted-foreground">SOURCE-DERIVED GUIDANCE</p>
          <CardTitle>Design with fidelity</CardTitle>
          <CardDescription>Rules derived from the existing bot renderer and Discord reference documentation.</CardDescription>
        </CardHeader>
        <CardContent>
          <Guidelines items={[...sourceGuidance]} />
        </CardContent>
      </Card>
    </div>
  );
}