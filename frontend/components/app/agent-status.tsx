'use client';

import { Mic } from 'lucide-react';
import { motion } from 'motion/react';
import { labels } from '@/lib/labels';
import { cn } from '@/lib/shadcn/utils';

export type KhataAgentState = 'listening' | 'speaking';

interface AgentStatusProps {
  state: KhataAgentState;
  className?: string;
}

/**
 * Distinct in-call status visuals:
 * - Listening: green breathing ring around a mic (calm, "I'm here")
 * - Speaking: maroon animated equalizer bars (the agent is talking)
 */
export function AgentStatus({ state, className }: AgentStatusProps) {
  const isSpeaking = state === 'speaking';
  const label = isSpeaking ? labels.speaking : labels.listening;

  return (
    <div
      data-khata-state={state}
      className={cn(
        'flex items-center gap-3 rounded-full border px-5 py-2.5 shadow-sm',
        isSpeaking
          ? 'border-[#7A1F2B]/30 bg-[#7A1F2B]/5 text-[#7A1F2B] dark:border-[#D9A441]/40 dark:bg-[#D9A441]/10 dark:text-[#D9A441]'
          : 'border-[#3D7A4F]/30 bg-[#3D7A4F]/5 text-[#3D7A4F] dark:border-[#6BB37E]/40 dark:bg-[#6BB37E]/10 dark:text-[#6BB37E]',
        className
      )}
    >
      {isSpeaking ? (
        <div className="flex h-6 items-end gap-0.5" aria-hidden>
          {[0, 1, 2, 3].map((i) => (
            <motion.span
              key={i}
              className="w-1 rounded-full bg-current"
              animate={{ height: [6, 22, 6] }}
              transition={{ duration: 0.65, repeat: Infinity, delay: i * 0.12, ease: 'easeInOut' }}
            />
          ))}
        </div>
      ) : (
        <span className="relative flex size-6 items-center justify-center" aria-hidden>
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-25" />
          <Mic className="relative size-4" />
        </span>
      )}
      <div className="text-left leading-tight">
        <p className="text-sm font-bold">{label.en}</p>
        <p className="text-xs opacity-80">{label.hi}</p>
      </div>
    </div>
  );
}
