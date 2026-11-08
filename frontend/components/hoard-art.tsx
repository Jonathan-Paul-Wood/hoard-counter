/** Custom dragon and hoard artwork in the application's existing line-art style. */
export function HoardMark() {
  return (
    <svg
      width="32"
      height="32"
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <ellipse cx="25" cy="27" rx="11" ry="4" />
      <path d="M14 27v15c0 5 22 5 22 0V27M14 32c0 5 22 5 22 0M14 37c0 5 22 5 22 0" />
      <ellipse cx="40" cy="36" rx="10" ry="3.5" />
      <path d="M30 36v10c0 5 20 5 20 0V36M36 41c5 2 11 1 14-1" />
      <path d="M50 17c11 11 12 28 2 36-9 8-34 8-43-2-5-6-4-13 1-18-2 10 3 17 15 18 16 2 26-4 27-13 1-6-1-11-4-15l-5-1 7-5Z" />
      <path d="m54 27 5-3-1 8 4 3-3 6M16 53l-2 4m11-3-1 5m11-6 1 5" />
    </svg>
  );
}

export function DragonCaveArt() {
  return (
    <svg
      viewBox="0 0 440 260"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <g stroke="#9bb09a" strokeWidth="1.3">
        <path d="M24 238c76-12 137-5 204 0s130 6 190-6" />
        <path d="m36 230 80-122 25 26 61-97 46 58 22-16 108 151M141 134l-25 60m86-157-8 101m54-43 27 47" />
      </g>
      <g stroke="#dce7d4" strokeWidth="1.6">
        <path d="M164 232c-3-40 2-83 27-100 17-11 43-6 57 13 16 23 18 59 15 89" />
        <path d="m153 226-5-36 8-36 24-29 32-7 30 11 22 30 12 40-1 35M158 154l16 6m67-31-9 15m33 17-13 7" />
      </g>
      <g stroke="#a0b997" strokeWidth="1.7">
        <path d="M196 208c4-19 18-30 34-29l15-14 1 19 15 4 12 14 30 5 7 12-10 10-40-1-20-10-18 7" />
        <path d="m217 181-5-15 16 11m4 2 5-20 7 10M249 204l9-3m-11 3 5 2M276 216l25 2m-18 1-3 6m14-6-3 7" />
        <circle cx="296" cy="212" r="1.5" fill="#a0b997" />
      </g>
      <path
        d="M316 218c11-12 21-2 27-17-1 12 13 14 22 9-4 16-20 20-32 13-7-4-11-3-17-5Z"
        stroke="#c7cba5"
        strokeWidth="1.6"
      />
      <path d="M323 218c12-4 17 3 26-1" stroke="#c7cba5" strokeWidth="1.2" />
      <g stroke="#8ca685" strokeWidth="1.2">
        <path d="m67 231 14-15 16 16m209 8 20-5 11 5M184 109l12-8 13 8m97 64 13 11 12 26" />
        <ellipse cx="199" cy="231" rx="10" ry="3" />
        <path d="M189 231v5c0 4 20 4 20 0v-5m6 3 14 1" />
      </g>
    </svg>
  );
}

type HoardIconProps = { size?: number; strokeWidth?: number };

export function HoardEggIcon({ size = 20, strokeWidth = 1.5 }: HoardIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 13c1-5-3-11-6-11S1 9 2 14c1 4 6 5 9 3M5 8l2 2-1 2 2 1" />
      <ellipse cx="16" cy="13" rx="6" ry="2" />
      <path d="M10 13v7c0 3 12 3 12 0v-7M10 15.4c0 3 12 3 12 0M10 17.7c0 3 12 3 12 0" />
    </svg>
  );
}

export function HoardCoinsIcon({
  size = 20,
  strokeWidth = 1.5,
}: HoardIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <ellipse cx="7" cy="4" rx="5" ry="2" />
      <path d="M2 4v15c0 2.7 10 2.7 10 0V4M2 7c0 2.7 10 2.7 10 0M2 10c0 2.7 10 2.7 10 0M2 13c0 2.7 10 2.7 10 0M2 16c0 2.7 10 2.7 10 0" />
      <ellipse cx="18" cy="13" rx="4" ry="2" />
      <path d="M14 13v6c0 2.7 8 2.7 8 0v-6M14 16c0 2.7 8 2.7 8 0" />
    </svg>
  );
}
