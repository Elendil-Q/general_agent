import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

export function FlipDisplay({
  uniqueKey,
  children,
  className,
}: {
  uniqueKey: string;
  children: React.ReactNode;
  className?: string;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, [uniqueKey]);

  return (
    <div className={cn("relative overflow-hidden", className)}>
      <div
        key={uniqueKey}
        className="transition-all duration-250 ease-out"
        style={{
          opacity: mounted ? 1 : 0,
          transform: mounted ? "translateY(0)" : "translateY(4px)",
        }}
      >
        {children}
      </div>
    </div>
  );
}
