'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTheme } from 'next-themes';
import { MediaDeviceFailure } from 'livekit-client';
import { Loader } from 'lucide-react';
import { AnimatePresence, type MotionProps, motion } from 'motion/react';
import { useSessionContext } from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import { AgentSessionView_01 } from '@/components/agents-ui/blocks/agent-session-view-01';
import { CallEndedView } from '@/components/app/call-ended-view';
import { WelcomeView } from '@/components/app/welcome-view';
import { labels } from '@/lib/labels';

const MotionWelcomeView = motion.create(WelcomeView);
const MotionCallEndedView = motion.create(CallEndedView);
const MotionSessionView = motion.create(AgentSessionView_01);

const VIEW_MOTION_PROPS: MotionProps = {
  variants: {
    visible: {
      opacity: 1,
    },
    hidden: {
      opacity: 0,
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: {
    duration: 0.5,
    ease: 'linear',
  },
};

/** Distinct Connecting state: ochre pulsing ring + spinner, bilingual label. */
function ConnectingView() {
  return (
    <section className="flex flex-col items-center justify-center gap-5">
      <span className="relative flex size-20 items-center justify-center">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-[#D9A441]/40" />
        <Loader className="relative size-9 animate-spin text-[#D9A441]" />
      </span>
      <div className="text-center leading-tight">
        <p className="text-xl font-bold">{labels.connecting.en}</p>
        <p className="text-muted-foreground text-sm">{labels.connecting.hi}</p>
      </div>
    </section>
  );
}

interface ViewControllerProps {
  appConfig: AppConfig;
}

export function ViewController({ appConfig }: ViewControllerProps) {
  const { isConnected, connectionState, start } = useSessionContext();
  const { resolvedTheme } = useTheme();
  const [hasConnected, setHasConnected] = useState(false);
  const [hasEnded, setHasEnded] = useState(false);
  const [micBlocked, setMicBlocked] = useState(false);

  useEffect(() => {
    if (isConnected) {
      setHasConnected(true);
      setHasEnded(false);
      setMicBlocked(false);
    }
  }, [isConnected]);

  useEffect(() => {
    if (hasConnected && connectionState === 'disconnected') {
      setHasEnded(true);
    }
  }, [connectionState, hasConnected]);

  const handleStartCall = useCallback(async () => {
    setMicBlocked(false);
    try {
      await start();
    } catch (error) {
      const failure = MediaDeviceFailure.getFailure(error);
      if (
        failure === MediaDeviceFailure.PermissionDenied ||
        (error instanceof DOMException && error.name === 'NotAllowedError')
      ) {
        setMicBlocked(true);
      } else {
        console.error('Failed to start session:', error);
      }
    }
  }, [start]);

  const handleStartAgain = useCallback(() => {
    setHasEnded(false);
    setHasConnected(false);
    setMicBlocked(false);
    void handleStartCall();
  }, [handleStartCall]);

  let view: React.ReactNode;
  if (isConnected) {
    view = (
      <MotionSessionView
        key="session-view"
        {...VIEW_MOTION_PROPS}
        supportsChatInput={appConfig.supportsChatInput}
        supportsVideoInput={appConfig.supportsVideoInput}
        supportsScreenShare={appConfig.supportsScreenShare}
        isPreConnectBufferEnabled={appConfig.isPreConnectBufferEnabled}
        audioVisualizerType={appConfig.audioVisualizerType}
        audioVisualizerColor={
          resolvedTheme === 'dark'
            ? appConfig.audioVisualizerColorDark
            : appConfig.audioVisualizerColor
        }
        audioVisualizerColorShift={appConfig.audioVisualizerColorShift}
        audioVisualizerBarCount={appConfig.audioVisualizerBarCount}
        audioVisualizerGridRowCount={appConfig.audioVisualizerGridRowCount}
        audioVisualizerGridColumnCount={appConfig.audioVisualizerGridColumnCount}
        audioVisualizerRadialBarCount={appConfig.audioVisualizerRadialBarCount}
        audioVisualizerRadialRadius={appConfig.audioVisualizerRadialRadius}
        audioVisualizerWaveLineWidth={appConfig.audioVisualizerWaveLineWidth}
        className="fixed inset-0"
      />
    );
  } else if (connectionState === 'connecting') {
    view = <ConnectingView key="connecting" />;
  } else if (hasEnded) {
    view = (
      <MotionCallEndedView
        key="call-ended"
        {...VIEW_MOTION_PROPS}
        onStartAgain={handleStartAgain}
      />
    );
  } else {
    view = (
      <MotionWelcomeView
        key="welcome"
        {...VIEW_MOTION_PROPS}
        startButtonText={appConfig.startButtonText}
        onStartCall={handleStartCall}
        micBlocked={micBlocked}
      />
    );
  }

  return <AnimatePresence mode="wait">{view}</AnimatePresence>;
}
