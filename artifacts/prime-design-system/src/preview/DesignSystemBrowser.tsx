import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Sun, Moon, Search } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { ScrollArea } from '../components/ui/scroll-area';
import primeTeamAvatar from '../assets/prime-team-avatar.png';
import {
  ALL_ENTRIES,
  DESIGN_SYSTEM,
  NAV_GROUPS,
  OVERVIEW_ENTRY,
  type NavGroup,
} from './registry';

function readHashId(): string {
  const id = new URLSearchParams(window.location.hash.slice(1)).get('page');
  if (!id) {
    return OVERVIEW_ENTRY.id;
  }
  return ALL_ENTRIES.some((entry) => entry.id === id)
    ? id
    : OVERVIEW_ENTRY.id;
}

function useSelectedId(): [string, (id: string) => void] {
  const [selected, setSelected] = useState(readHashId);

  useEffect(() => {
    const onHashChange = () => setSelected(readHashId());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const select = (id: string) => {
    setSelected(id);
    window.location.hash = new URLSearchParams({ page: id }).toString();
  };

  return [selected, select];
}

function NavigationItems({
  showOverview,
  groups,
  activeId,
  query,
  select,
}: {
  showOverview: boolean;
  groups: NavGroup[];
  activeId: string;
  query: string;
  select: (id: string) => void;
}) {
  return (
    <nav aria-label="Design system navigation" className="space-y-6 py-3">
      {showOverview ? (
        <button
          type="button"
          onClick={() => select(OVERVIEW_ENTRY.id)}
          aria-current={OVERVIEW_ENTRY.id === activeId}
          className="group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors hover:bg-sidebar-accent aria-[current=true]:bg-sidebar-primary aria-[current=true]:text-sidebar-primary-foreground"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground group-aria-[current=true]:bg-sidebar-primary-foreground" />
          {OVERVIEW_ENTRY.name}
        </button>
      ) : null}

      {groups.map((group) => (
        <div key={group.name}>
          <p className="px-3 text-xs font-bold uppercase tracking-wide text-muted-foreground">
            {group.name}
          </p>
          <div className="mt-2 space-y-1">
            {group.entries.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => select(entry.id)}
                aria-current={entry.id === activeId}
                className="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent aria-[current=true]:bg-sidebar-primary aria-[current=true]:font-medium aria-[current=true]:text-sidebar-primary-foreground"
              >
                <span className="h-1 w-1 rounded-full bg-border group-aria-[current=true]:bg-sidebar-primary-foreground" />
                {entry.name}
              </button>
            ))}
          </div>
        </div>
      ))}

      {!showOverview && groups.length === 0 ? (
        <p className="px-2 py-4 text-sm text-muted-foreground">
          No sections match “{query}”.
        </p>
      ) : null}
    </nav>
  );
}

export function DesignSystemBrowser() {
  const [selectedId, select] = useSelectedId();
  const [query, setQuery] = useState('');
  const mobileNav = useRef<HTMLDetailsElement>(null);
  const mobileNavSummary = useRef<HTMLElement>(null);
  const [darkMode, setDarkMode] = useState(true);
  const normalizedQuery = query.trim().toLowerCase();

  const filteredGroups = useMemo(
    () =>
      NAV_GROUPS.map((group) => ({
        ...group,
        entries: group.name.toLowerCase().includes(normalizedQuery)
          ? group.entries
          : group.entries.filter((entry) =>
              `${entry.name} ${entry.description}`
                .toLowerCase()
                .includes(normalizedQuery),
            ),
      })).filter((group) => group.entries.length > 0),
    [normalizedQuery],
  );

  const active =
    ALL_ENTRIES.find((entry) => entry.id === selectedId) ?? OVERVIEW_ENTRY;
  const activeGroup = NAV_GROUPS.find((group) =>
    group.entries.some((entry) => entry.id === active.id),
  );
  const ActivePage = active.Page;

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [active.id]);

  const showOverview = `${OVERVIEW_ENTRY.name} ${OVERVIEW_ENTRY.description}`
    .toLowerCase()
    .includes(normalizedQuery);
  const selectPage = (id: string) => {
    select(id);
    if (mobileNav.current?.open) {
      mobileNav.current.removeAttribute('open');
      mobileNavSummary.current?.focus();
    }
  };

  return (
    <div className={`${darkMode ? 'dark' : ''} min-h-screen bg-background text-foreground`}>
      <div className="min-h-screen md:grid md:grid-cols-[272px_minmax(0,1fr)]">
      <aside className="border-b border-sidebar-border bg-sidebar text-sidebar-foreground md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-b-0 md:border-r">
        <div className="border-b border-sidebar-border px-5 py-5">
          <div className="flex items-center gap-3">
            <img src={primeTeamAvatar} alt="PRIME TEAM avatar" className="h-10 w-10 rounded-xl object-cover" />
            <div className="min-w-0">
              <p className="truncate text-sm font-bold tracking-tight">{DESIGN_SYSTEM.title}</p>
              <p className="mt-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Foundations · Components</p>
            </div>
          </div>
        </div>
        <div className="px-4 py-4">
          <div className="relative">
            <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search design system"
              placeholder="Search foundations, components…"
              className="h-10 border-sidebar-border bg-background pl-9 text-sm"
            />
          </div>
        </div>
        <ScrollArea className="hidden min-h-0 flex-1 px-4 pb-5 md:block">
          <NavigationItems
            showOverview={showOverview}
            groups={filteredGroups}
            activeId={active.id}
            query={query}
            select={selectPage}
          />
        </ScrollArea>
        <details ref={mobileNav} className="border-t border-sidebar-border px-4 py-3 md:hidden">
          <summary
            ref={mobileNavSummary}
            className="cursor-pointer text-sm font-medium"
          >
            Browse sections:{' '}
            <span className="text-muted-foreground">{active.name}</span>
          </summary>
          <ScrollArea className="mt-3 h-64 pb-2">
            <NavigationItems
              showOverview={showOverview}
              groups={filteredGroups}
              activeId={active.id}
              query={query}
              select={selectPage}
            />
          </ScrollArea>
        </details>
      </aside>

      <main className="min-w-0">
        <div className="mx-auto max-w-7xl px-5 py-6 sm:px-8 lg:px-12">
          <div className="mb-9 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-semibold uppercase tracking-wide text-primary">PRIME</span>
              <span className="text-border">/</span>
              <span>{activeGroup?.name ?? 'Start here'}</span>
              {active.id !== OVERVIEW_ENTRY.id ? <><span className="text-border">/</span><span className="text-foreground">{active.name}</span></> : null}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDarkMode((current) => !current)}
              aria-label={`Switch to ${darkMode ? 'light' : 'dark'} mode`}
              className="gap-2 border-border bg-card"
            >
              {darkMode ? <Sun aria-hidden="true" className="h-4 w-4" /> : <Moon aria-hidden="true" className="h-4 w-4" />}
              <span>{darkMode ? 'Light mode' : 'Dark mode'}</span>
            </Button>
          </div>
          <header className="border-b border-border pb-8">
            {active.id === OVERVIEW_ENTRY.id ? (
              <>
                <p className="mb-3 text-xs font-bold uppercase tracking-widest text-accent">PRIME / VISUAL LANGUAGE</p>
                <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">
                  {DESIGN_SYSTEM.title}<span className="text-primary">.</span>
                </h1>
                <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
                  {DESIGN_SYSTEM.description}
                </p>
              </>
            ) : (
              <>
                <p className="text-xs font-bold uppercase tracking-widest text-accent">
                  {activeGroup?.name}
                </p>
                <h1 className="mt-3 text-3xl font-bold tracking-tight">{active.name}</h1>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
                  {active.description}
                </p>
              </>
            )}
          </header>

          <div className="pt-8">
            <Suspense
              fallback={
                <div
                  role="status"
                  className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground"
                >
                  Loading preview…
                </div>
              }
            >
              <ActivePage />
            </Suspense>
          </div>
        </div>
      </main>
      </div>
    </div>
  );
}
