import { Mic, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { labels } from '@/lib/labels';

function WelcomeImage() {
  return (
    <svg
      width="64"
      height="64"
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="text-primary mb-4 size-16"
    >
      <rect x="4" y="6" width="24" height="20" rx="3" fill="currentColor" opacity="0.15" />
      <path d="M8 12h16M8 16h16M8 20h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="8" cy="10" r="1.5" fill="currentColor" />
    </svg>
  );
}

/** Small drawing of a browser address bar pointing at the permission (lock/mic) icon. */
function MicPermissionPointer() {
  return (
    <div className="mt-3">
      <svg
        viewBox="0 0 260 64"
        className="mx-auto h-16 w-full max-w-xs text-foreground"
        role="img"
        aria-label={labels.micIconPointer.en}
      >
        {/* address bar */}
        <rect x="4" y="6" width="252" height="30" rx="8" fill="currentColor" opacity="0.08" />
        <rect x="4" y="6" width="252" height="30" rx="8" stroke="currentColor" opacity="0.35" />
        {/* padlock */}
        <path d="M24 22v-4a6 6 0 0 1 12 0v4" fill="none" stroke="currentColor" strokeWidth="2" />
        <rect x="21" y="22" width="18" height="12" rx="2.5" fill="currentColor" />
        {/* url pill */}
        <rect x="50" y="14" width="120" height="14" rx="7" fill="currentColor" opacity="0.18" />
        {/* pointer arrow up toward the padlock */}
        <path
          d="M30 40 v12 m0 0 l-6 -6 m6 6 l6 -6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <text x="60" y="58" fontSize="11" fill="currentColor" opacity="0.7">
          {labels.micIconPointer.en}
        </text>
      </svg>
    </div>
  );
}

interface WelcomeViewProps {
  startButtonText?: string;
  onStartCall: () => void;
  micBlocked?: boolean;
}

export const WelcomeView = ({
  startButtonText,
  onStartCall,
  micBlocked = false,
  ref,
}: React.ComponentProps<'div'> & WelcomeViewProps) => {
  return (
    <div ref={ref}>
      <section className="bg-background flex flex-col items-center justify-center px-6 text-center">
        <WelcomeImage />

        <h1 className="text-foreground text-2xl font-bold">{labels.brand.en}</h1>
        <p className="text-foreground/70 text-lg leading-6 font-medium">
          {labels.tagline.en} · {labels.tagline.hi}
        </p>

        <Button
          size="lg"
          onClick={onStartCall}
          className="mt-8 w-80 rounded-2xl px-8 py-8 text-base font-bold shadow-lg"
        >
          <Mic className="size-6" />
          <span className="flex flex-col items-center leading-tight">
            <span>{labels.start.en}</span>
            <span className="text-xs font-medium opacity-80">{labels.start.hi}</span>
          </span>
        </Button>

        {micBlocked && (
          <div
            role="alert"
            className="mt-6 w-full max-w-md rounded-2xl border border-destructive/40 bg-destructive/5 p-4 text-left"
          >
            <div className="flex items-center gap-2">
              <ShieldAlert className="size-5 shrink-0 text-destructive" />
              <p className="text-destructive text-sm font-bold">{labels.micBlockedTitle.en}</p>
            </div>
            <p className="text-sm">{labels.micBlockedBody.en}</p>
            <p className="text-xs opacity-80">{labels.micBlockedBody.hi}</p>
            <ul className="mt-2 list-inside list-disc space-y-1 text-sm">
              <li>{labels.micBlockedFix.en}</li>
              <li className="text-xs opacity-80">{labels.micBlockedFix.hi}</li>
            </ul>
            <MicPermissionPointer />
          </div>
        )}
      </section>
    </div>
  );
};
