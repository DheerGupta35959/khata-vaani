export interface Bilingual {
  en: string;
  hi: string;
}

export const labels = {
  brand: { en: 'Khata-Vaani', hi: 'खाता-वाणी' },
  tagline: {
    en: 'Bolkar likhein apna khata',
    hi: 'बोलकर लिखें अपना खाता',
  },
  ready: { en: 'Ready', hi: 'तैयार' },
  start: { en: 'Start Khata', hi: 'Khata Shuru Karein' },
  startAgain: { en: 'Start Again', hi: 'Phir Se Shuru Karein' },
  connecting: { en: 'Connecting...', hi: 'Jod Rahe Hain...' },
  listening: { en: 'Listening', hi: 'Sun Rahe Hain' },
  listeningHint: {
    en: "Go ahead, I'm listening",
    hi: 'Boliye, main sun raha hoon',
  },
  speaking: { en: 'Speaking', hi: 'Bol Rahe Hain' },
  callEndedTitle: { en: 'Call Ended', hi: 'Call Khatam' },
  callEndedBody: {
    en: 'Aur kuch likhna ho to khata phir se shuru karein.',
    hi: 'Aur kuch likhna ho to khata phir se shuru karein.',
  },
  micBlockedTitle: { en: 'Microphone is blocked', hi: 'Microphone band hai' },
  micBlockedBody: {
    en: 'Khata-Vaani cannot hear you because the browser is blocking the microphone.',
    hi: 'Khata-Vaani aapki awaaz nahi sun sakta kyunki browser microphone ko roak raha hai.',
  },
  micBlockedFix: {
    en: 'Click the lock or mic icon in the address bar at the top, choose "Allow" for Microphone, then press Start Khata again.',
    hi: 'Upar address bar me lock ya mic icon par click karein, Microphone ke liye "Allow" chunein, phir Start Khata dobara dabayein.',
  },
  micIconPointer: { en: 'Permission icon lives here', hi: 'Permission icon yahan hota hai' },
  tapToHear: { en: 'Tap to hear audio', hi: 'Awaaz sunne ke liye dabayein' },
  endCall: { en: 'End Call', hi: 'Call Band Karein' },
} as const;

export type LabelKey = keyof typeof labels;
