"use client";

import { useEffect, useState } from "react";

interface WordRotateProps {
  words: string[];
  duration?: number;
  className?: string;
}

/**
 * Lightweight word rotation using setInterval — no motion library.
 * Shows one word at a time with a CSS fade-slide transition.
 */
export function WordRotate({
  words,
  duration = 2500,
  className,
}: WordRotateProps) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (words.length <= 1) return;
    const interval = setInterval(() => {
      setIndex((i) => (i + 1) % words.length);
    }, duration);
    return () => clearInterval(interval);
  }, [words.length, duration]);

  return (
    <span className={className}>
      <span
        key={index}
        className="word-rotate-item inline-block"
        style={{ animationDuration: `${duration}ms` }}
      >
        {words[index]}
      </span>
    </span>
  );
}
