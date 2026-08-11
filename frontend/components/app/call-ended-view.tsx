import { PhoneOff, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { labels } from '@/lib/labels';

interface CallEndedViewProps {
  onStartAgain: () => void;
}

export const CallEndedView = ({
  onStartAgain,
  ref,
}: React.ComponentProps<'div'> & CallEndedViewProps) => {
  return (
    <div ref={ref}>
      <section className="bg-background flex flex-col items-center justify-center px-6 text-center">
        <div className="bg-muted mb-4 flex size-20 items-center justify-center rounded-full">
          <PhoneOff className="text-muted-foreground size-10" />
        </div>

        <h1 className="text-foreground text-xl font-bold">{labels.callEndedTitle.en}</h1>
        <p className="text-muted-foreground text-sm">{labels.callEndedTitle.hi}</p>
        <p className="text-muted-foreground mt-3 max-w-sm text-sm">{labels.callEndedBody.en}</p>

        <Button
          size="lg"
          onClick={onStartAgain}
          className="mt-8 w-80 rounded-2xl px-8 py-7 text-base font-bold shadow-lg"
        >
          <RotateCcw className="size-5" />
          <span className="flex flex-col items-center leading-tight">
            <span>{labels.startAgain.en}</span>
            <span className="text-xs font-medium opacity-80">{labels.startAgain.hi}</span>
          </span>
        </Button>
      </section>
    </div>
  );
};
