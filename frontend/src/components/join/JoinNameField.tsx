import { KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import Button from "@/components/ui/Button";
import Spinner from "@/components/ui/Spinner";
import { JoinGroup, groupLabel, isVcs } from "@/lib/joinGroup";
import { OWNER_TAG_MAX, joinPickerName } from "@/lib/ownerTag";

type Props = {
  group: JoinGroup;
  names: string[];
  namesLoading: boolean;
  namesError: string | null;
  value: string;
  onChange: (value: string) => void;
  onRetry: () => void;
};

type Suggestion = { key: string; name: string; kind: "existing" | "new" };

function rankMatches(names: string[], q: string): string[] {
  if (!q) return names;
  const exact: string[] = [];
  const prefix: string[] = [];
  const rest: string[] = [];
  for (const name of names) {
    const lower = name.toLowerCase();
    if (lower === q) exact.push(name);
    else if (lower.startsWith(q)) prefix.push(name);
    else if (lower.includes(q)) rest.push(name);
  }
  return [...exact, ...prefix, ...rest];
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(query);
  if (idx < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-transparent font-semibold text-indigo-200">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  );
}

function suggestionStatus(
  exactName: string | null,
  picked: string | null,
  matchCount: number,
  nameCount: number
): string {
  if (exactName) return `Existing VCS name. Submit will use ${exactName}.`;
  if (picked && matchCount) return `No exact match. Pick an existing name below, or submit to add ${picked}.`;
  if (picked) return `No existing VCS name matches. Submit will add ${picked}.`;
  if (nameCount) return "Type to see existing names, or enter a new one.";
  return "Type a new VCS name.";
}

function SbNameSelect({
  group,
  names,
  namesLoading,
  namesError,
  value,
  onChange,
  onRetry,
}: Props) {
  return (
    <label className="flex min-w-0 w-full flex-col gap-1.5">
      <span className="text-xs font-medium text-gray-400">Your name</span>
      {namesLoading && names.length === 0 ? (
        <div className="flex items-center gap-2 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-400">
          <Spinner className="h-4 w-4" />
          Loading names…
        </div>
      ) : names.length === 0 && namesError ? (
        <div className="flex flex-col gap-2">
          <p className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200">{namesError}</p>
          <Button type="button" variant="secondary" onClick={onRetry} className="w-full">
            Retry
          </Button>
        </div>
      ) : (
        <select
          id="join-name"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          required
          disabled={names.length === 0}
          className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-gray-100 outline-none focus:border-accent disabled:opacity-60"
        >
          <option value="">
            {names.length ? "Select from the dropdown…" : `No ${groupLabel(group)} names available`}
          </option>
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      )}
      {names.length > 0 ? (
        <p className="text-xs text-gray-500">Use the dropdown. Custom names are not allowed.</p>
      ) : (
        !namesLoading &&
        !namesError && (
          <p className="text-xs text-gray-500">
            No names are enrolled for {groupLabel(group)}. Ask an admin to add yours.
          </p>
        )
      )}
    </label>
  );
}

function VcsNameCombobox({ names, namesLoading, namesError, value, onChange, onRetry }: Props) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const navigated = useRef(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const picked = joinPickerName(value);
  const q = value.trim().toLowerCase();
  const matches = useMemo(() => rankMatches(names, q), [names, q]);
  const exactName = picked ? names.find((name) => name.toLowerCase() === picked.toLowerCase()) ?? null : null;
  const options = useMemo<Suggestion[]>(() => {
    const rows: Suggestion[] = matches.map((name) => ({
      key: `existing:${name}`,
      name,
      kind: "existing",
    }));
    if (picked && !exactName) rows.push({ key: `new:${picked}`, name: picked, kind: "new" });
    return rows;
  }, [exactName, matches, picked]);

  useEffect(() => {
    function onDoc(event: MouseEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => {
    navigated.current = false;
    setActive(0);
  }, [q]);

  useEffect(() => {
    setActive((index) => {
      if (!options.length) return 0;
      return Math.min(index, options.length - 1);
    });
  }, [options.length]);

  function choose(name: string) {
    onChange(name);
    navigated.current = false;
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!options.length) {
        setOpen(true);
        return;
      }
      if (!open) {
        setOpen(true);
        setActive(0);
        navigated.current = true;
        return;
      }
      navigated.current = true;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => (index + step + options.length) % options.length);
      return;
    }
    if (event.key === "Enter" && open && navigated.current && options[active]) {
      const name = options[active].name;
      const same = joinPickerName(name) === picked || name === value.trim();
      if (!same) {
        event.preventDefault();
        choose(name);
      } else {
        setOpen(false);
      }
    }
  }

  const status = namesError ? null : suggestionStatus(exactName, picked, matches.length, names.length);

  return (
    <div ref={wrapRef} className="flex min-w-0 w-full flex-col gap-1.5">
      <label htmlFor="join-name" className="text-xs font-medium text-gray-400">
        Your name
      </label>
      <div className="relative">
        <input
          id="join-name"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          maxLength={OWNER_TAG_MAX}
          value={value}
          onChange={(event) => {
            navigated.current = false;
            onChange(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={names.length ? "Start typing to see existing names…" : "Type a new name…"}
          className="w-full rounded-lg border border-surface-border bg-surface py-2 pl-3 pr-10 text-sm text-gray-100 outline-none focus:border-accent"
        />
        <button
          type="button"
          tabIndex={-1}
          aria-label={open ? "Hide name suggestions" : "Show name suggestions"}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => setOpen((current) => !current)}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-gray-400 hover:bg-white/[0.06] hover:text-gray-200"
        >
          {namesLoading ? <Spinner className="h-4 w-4" /> : <ChevronDown size={16} className={open ? "rotate-180" : ""} />}
        </button>
      </div>
      {open && (
        <div
          id={listId}
          role="listbox"
          className="max-h-52 overflow-auto rounded-lg border border-white/[0.08] bg-surface py-1 shadow-lg"
        >
          {!q && names.length > 0 && (
            <p className="px-3 py-1 text-[11px] uppercase tracking-wide text-gray-500">Existing VCS names</p>
          )}
          {options.map((option, index) => (
            <button
              key={option.key}
              id={`${listId}-${index}`}
              type="button"
              role="option"
              aria-selected={index === active}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(option.name)}
              className={`flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-sm ${
                index === active ? "bg-white/[0.06]" : "hover:bg-white/[0.04]"
              }`}
            >
              <span className="text-gray-100">
                {option.kind === "new" ? `Add ${option.name}` : <Highlight text={option.name} query={q} />}
              </span>
              <span
                className={`shrink-0 text-[10px] font-medium uppercase tracking-wide ${
                  option.kind === "existing" ? "text-emerald-300/90" : "text-indigo-300"
                }`}
              >
                {option.kind === "existing" ? "Existing" : "New"}
              </span>
            </button>
          ))}
          {options.length === 0 && (
            <p className="px-3 py-2 text-sm text-gray-500">
              {namesLoading ? "Loading existing names…" : "No existing VCS names yet. Type a new one."}
            </p>
          )}
        </div>
      )}
      {namesError ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-xs text-amber-300">Could not load existing names. You can still type a new one.</p>
          <button type="button" onClick={onRetry} className="self-start text-xs text-indigo-300 hover:text-indigo-200">
            Retry list
          </button>
        </div>
      ) : (
        <p className={`text-xs ${exactName ? "text-emerald-300/90" : "text-gray-500"}`}>{status}</p>
      )}
    </div>
  );
}

export default function JoinNameField(props: Props) {
  return isVcs(props.group) ? <VcsNameCombobox {...props} /> : <SbNameSelect {...props} />;
}
