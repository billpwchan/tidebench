import { useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { Source } from '../api';
import type { ResearchInputs } from '../proApi';

const MAX_DRAFT_BYTES = 1024 * 1024;
const PREFIX = 'tidebench:research-draft:v1';
export const researchDraftKey = (userId: string, source: string, form: string) =>
  `${PREFIX}:${encodeURIComponent(userId)}:${source}:${form}`;
export const inputHandoffKey = (value: unknown) => (value ? JSON.stringify(value) : '');
export const isDraftRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);

// Package responses include source/index metadata and may represent absent
// optional datasets as null. Check that contract, then retain the fields this
// research form submits. Unknown fields and a different source remain invalid.
export function normalizeResearchInputs(
  value: unknown,
  source: Source,
): ResearchInputs | undefined {
  if (!isDraftRecord(value) || typeof value.dataset_id !== 'string' || !value.dataset_id)
    return undefined;
  const datasets = [
    'mark_dataset_id',
    'funding_dataset_id',
    'index_dataset_id',
    'package_id',
    'package_manifest_hash',
  ];
  if (
    !Object.entries(value).every(([key, item]) => {
      if (key === 'dataset_id') return typeof item === 'string';
      if (key === 'source') return item === source;
      if (key === 'start_ts' || key === 'end_ts')
        return item === undefined || (typeof item === 'number' && Number.isFinite(item));
      return datasets.includes(key) && (item == null || typeof item === 'string');
    })
  )
    return undefined;
  const inputs: ResearchInputs = { dataset_id: value.dataset_id };
  for (const key of [
    'mark_dataset_id',
    'funding_dataset_id',
    'package_id',
    'package_manifest_hash',
  ] as const) {
    if (typeof value[key] === 'string') inputs[key] = value[key];
  }
  for (const key of ['start_ts', 'end_ts'] as const) {
    if (typeof value[key] === 'number') inputs[key] = value[key];
  }
  return inputs;
}

// Validate the stored shape before any value reaches controlled fields. Optional
// domain fields receive a stricter validator in their owning form.
export function matchesDraftShape(value: unknown, template: unknown): boolean {
  if (template === undefined)
    return value === undefined || value === null || isSafeDraftValue(value);
  if (typeof template === 'number') return typeof value === 'number' && Number.isFinite(value);
  if (typeof template === 'string')
    return typeof value === 'string' && value.length <= MAX_DRAFT_BYTES;
  if (typeof template === 'boolean') return typeof value === 'boolean';
  if (Array.isArray(template))
    return (
      Array.isArray(value) &&
      value.length <= 200 &&
      value.every((item) =>
        template.length ? matchesDraftShape(item, template[0]) : isSafeDraftValue(item),
      )
    );
  if (isDraftRecord(template))
    return (
      isDraftRecord(value) &&
      Object.entries(template).every(([key, item]) => matchesDraftShape(value[key], item))
    );
  return value === template;
}
function isSafeDraftValue(value: unknown, depth = 0): boolean {
  if (depth > 16) return false;
  if (value === null || value === undefined || typeof value === 'boolean') return true;
  if (typeof value === 'string') return value.length <= MAX_DRAFT_BYTES;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value))
    return value.length <= 1000 && value.every((v) => isSafeDraftValue(v, depth + 1));
  return (
    isDraftRecord(value) &&
    Object.entries(value).length <= 100 &&
    Object.entries(value).every(
      ([key, v]) =>
        !['__proto__', 'constructor', 'prototype'].includes(key) && isSafeDraftValue(v, depth + 1),
    )
  );
}

export function useResearchDraft<T extends object>(
  form: string,
  userId: string,
  source: string,
  defaults: () => T,
  validate: (value: unknown) => value is T,
) {
  const key = researchDraftKey(userId, source, form);
  const read = () => {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw)
        return {
          storageKey: key,
          value: defaults(),
          dirty: false,
          restoredAt: null as number | null,
          problem: '',
          invalidRaw: null as string | null,
        };
      let saved: unknown = null;
      if (raw.length <= MAX_DRAFT_BYTES) {
        try {
          saved = JSON.parse(raw);
        } catch {
          // Malformed JSON is damaged content, rather than a storage failure.
          // Reading stays pure: only a committed invalid state removes its key.
        }
      }
      if (
        !isDraftRecord(saved) ||
        saved.schema !== 1 ||
        saved.userId !== userId ||
        saved.source !== source ||
        saved.form !== form ||
        typeof saved.savedAt !== 'number' ||
        !Number.isFinite(saved.savedAt) ||
        !isSafeDraftValue(saved.value) ||
        !validate(saved.value)
      ) {
        return {
          storageKey: key,
          value: defaults(),
          dirty: false,
          restoredAt: null,
          problem: 'invalid',
          invalidRaw: raw,
        };
      }
      return {
        storageKey: key,
        value: saved.value,
        dirty: false,
        restoredAt: saved.savedAt,
        problem: '',
        invalidRaw: null,
      };
    } catch {
      return {
        storageKey: key,
        value: defaults(),
        dirty: false,
        restoredAt: null,
        problem: 'unavailable',
        invalidRaw: null,
      };
    }
  };
  const [state, setState] = useState(read);
  const current = state.storageKey === key ? state : read();
  const lastSaved = useRef('');
  useEffect(() => {
    if (state.storageKey !== key) {
      lastSaved.current = '';
      setState(current);
    }
  }, [key]);
  useEffect(() => {
    if (state.storageKey !== key || state.problem !== 'invalid' || state.invalidRaw === null)
      return;
    try {
      // An abandoned render must not consume either the damaged value or its
      // notice. A repair saved before this effect commits must also survive.
      if (sessionStorage.getItem(key) === state.invalidRaw) sessionStorage.removeItem(key);
    } catch {
      setState((old) =>
        old.storageKey === key && old.problem === 'invalid'
          ? { ...old, problem: 'unavailable' }
          : old,
      );
    }
  }, [key, state.storageKey, state.problem, state.invalidRaw]);
  const persist = (value: T) => {
    try {
      const payload = JSON.stringify(value);
      if (payload === lastSaved.current) return;
      const raw = JSON.stringify({ schema: 1, userId, source, form, savedAt: Date.now(), value });
      if (raw.length > MAX_DRAFT_BYTES) throw new Error('draft capacity');
      sessionStorage.setItem(key, raw);
      lastSaved.current = payload;
      if (state.problem === 'unavailable') setState((old) => ({ ...old, problem: '' }));
    } catch {
      setState((old) => (old.problem === 'unavailable' ? old : { ...old, problem: 'unavailable' }));
    }
  };
  useEffect(() => {
    if (state.storageKey === key && state.dirty) persist(state.value);
  }, [state.value, state.dirty, key]);
  const field = <K extends keyof T>(name: K): [T[K], Dispatch<SetStateAction<T[K]>>] => [
    current.value[name],
    (next) =>
      setState((old) => {
        const base = old.storageKey === key ? old : current;
        return {
          ...base,
          dirty: true,
          value: {
            ...base.value,
            [name]:
              typeof next === 'function' ? (next as (value: T[K]) => T[K])(base.value[name]) : next,
          },
        };
      }),
  ];
  const clear = (patch: Partial<T> = {}) => {
    let problem = '';
    try {
      sessionStorage.removeItem(key);
    } catch {
      problem = 'unavailable';
    }
    lastSaved.current = '';
    setState({
      storageKey: key,
      value: { ...defaults(), ...patch },
      dirty: false,
      restoredAt: null,
      problem,
      invalidRaw: null,
    });
  };
  // Data-page handoffs must survive the synchronous navigation that follows.
  const flush = (patch: Partial<T> = {}) => {
    const value = { ...current.value, ...patch };
    setState({ ...current, value, dirty: true });
    persist(value);
  };
  return {
    field,
    value: current.value,
    restoredAt: current.restoredAt,
    problem: current.problem,
    hasDraft: current.dirty || current.restoredAt !== null,
    clear,
    flush,
  };
}
