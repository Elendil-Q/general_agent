"use client";

import { Button } from "@/components/ui/button";
import { writeTextToClipboard } from "@/core/clipboard";
import { cn } from "@/lib/utils";
import { CheckIcon, CopyIcon } from "lucide-react";
import {
  type ComponentProps,
  createContext,
  type HTMLAttributes,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import hljs from "highlight.js/lib/common";

import "./code-block.css";

// Highlight.js theme CSS is loaded globally via globals.css.

type CodeBlockProps = HTMLAttributes<HTMLDivElement> & {
  code: string;
  language: string;
  showLineNumbers?: boolean;
};

type CodeBlockContextType = {
  code: string;
};

const CodeBlockContext = createContext<CodeBlockContextType>({
  code: "",
});

function highlightWithLineNumbers(
  code: string,
  language: string,
  showLineNumbers: boolean,
): string {
  let highlighted: string;
  try {
    highlighted =
      language && hljs.getLanguage(language)
        ? hljs.highlight(code, { language }).value
        : hljs.highlightAuto(code).value;
  } catch {
    highlighted = hljs.highlightAuto(code).value;
  }

  if (!showLineNumbers) return highlighted;

  const lines = highlighted.split("\n");
  return lines
    .map(
      (line, i) =>
        `<span class="hljs-ln-num inline-block min-w-10 mr-4 text-right select-none text-muted-foreground">${i + 1}</span>${line}`,
    )
    .join("\n");
}

export function highlightCode(
  code: string,
  language: string,
  showLineNumbers = false,
): [string, string] {
  const html = highlightWithLineNumbers(code, language, showLineNumbers);
  // Wrap in <pre><code class="hljs"> so highlight.js theme CSS applies.
  return [
    `<pre class="hljs"><code class="hljs hljs-light">${html}</code></pre>`,
    `<pre class="hljs"><code class="hljs hljs-dark">${html}</code></pre>`,
  ];
}

export const CodeBlock = ({
  code,
  language,
  showLineNumbers = false,
  className,
  children,
  ...props
}: CodeBlockProps) => {
  const [html, setHtml] = useState<string>("");
  const [darkHtml, setDarkHtml] = useState<string>("");

  useEffect(() => {
    const [light, dark] = highlightCode(code, language, showLineNumbers);
    setHtml(light);
    setDarkHtml(dark);
  }, [code, language, showLineNumbers]);

  return (
    <CodeBlockContext.Provider value={{ code }}>
      <div
        className={cn(
          "group bg-background text-foreground relative size-full overflow-hidden rounded-md border",
          className,
        )}
        {...props}
      >
        <div className="relative size-full">
          <div
            className="[&>pre]:bg-background! [&>pre]:text-foreground! size-full overflow-auto dark:hidden [&_code]:font-mono [&_code]:text-sm [&>pre]:m-0 [&>pre]:text-sm [&>pre]:whitespace-pre-wrap"
            // biome-ignore lint/security/noDangerouslySetInnerHtml: "this is needed."
            dangerouslySetInnerHTML={{ __html: html }}
          />
          <div
            className="[&>pre]:bg-background! [&>pre]:text-foreground! hidden size-full overflow-auto dark:block [&_code]:font-mono [&_code]:text-sm [&>pre]:m-0 [&>pre]:text-sm [&>pre]:whitespace-pre-wrap"
            // biome-ignore lint/security/noDangerouslySetInnerHtml: "this is needed."
            dangerouslySetInnerHTML={{ __html: darkHtml }}
          />
          {children && (
            <div className="absolute top-2 right-2 flex items-center gap-2">
              {children}
            </div>
          )}
        </div>
      </div>
    </CodeBlockContext.Provider>
  );
};

export type CodeBlockCopyButtonProps = ComponentProps<typeof Button> & {
  onCopy?: () => void;
  onError?: (error: Error) => void;
  timeout?: number;
};

export const CodeBlockCopyButton = ({
  onCopy,
  onError,
  timeout = 2000,
  children,
  className,
  ...props
}: CodeBlockCopyButtonProps) => {
  const [isCopied, setIsCopied] = useState(false);
  const { code } = useContext(CodeBlockContext);

  const copyToClipboard = () => {
    void (async () => {
      const didCopy = await writeTextToClipboard(code);
      if (!didCopy) {
        onError?.(new Error("Clipboard API not available"));
        return;
      }

      setIsCopied(true);
      onCopy?.();
      setTimeout(() => setIsCopied(false), timeout);
    })().catch((error) => {
      onError?.(error as Error);
    });
  };

  const Icon = isCopied ? CheckIcon : CopyIcon;

  return (
    <Button
      className={cn("shrink-0", className)}
      onClick={copyToClipboard}
      size="icon"
      variant="ghost"
      {...props}
    >
      {children ?? <Icon size={14} />}
    </Button>
  );
};
