import { useEffect, useRef } from 'react';

export type ReceiptTask = {
  context: string;
  generation: number;
  lane: string;
  serial: number;
};

// Server writes outlive the view that requested them. Only their UI receipt belongs
// to this mounted task; navigation must invalidate it even when the user returns.
export function useReceiptOwnership(context: string) {
  const owner = useRef({
    context,
    mounted: false,
    generation: 0,
    serials: new Map<string, number>(),
  });
  if (owner.current.context !== context) {
    owner.current.context = context;
    owner.current.generation += 1;
  }
  const invalidate = () => {
    owner.current.generation += 1;
  };
  useEffect(() => {
    owner.current.mounted = true;
    const events = ['tidebench:navigation', 'hashchange', 'popstate'];
    for (const event of events) window.addEventListener(event, invalidate);
    return () => {
      owner.current.mounted = false;
      invalidate();
      for (const event of events) window.removeEventListener(event, invalidate);
    };
  }, []);
  const capture = (lane = 'default'): ReceiptTask => {
    const current = owner.current;
    const serial = (current.serials.get(lane) ?? 0) + 1;
    current.serials.set(lane, serial);
    return { context: current.context, generation: current.generation, lane, serial };
  };
  const owns = (task?: ReceiptTask) => {
    const current = owner.current;
    return (
      !!task &&
      current.mounted &&
      task.context === current.context &&
      task.generation === current.generation &&
      task.serial === current.serials.get(task.lane)
    );
  };
  return { capture, invalidate, owns };
}
