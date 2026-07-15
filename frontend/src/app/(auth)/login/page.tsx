"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { DeerGridBg } from "@/components/ui/deer-grid-bg";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  canCreateRegularAccount,
  fetchSetupStatus,
  isSystemAlreadyInitializedError,
  type SetupStatusResponse,
} from "@/core/auth/setup";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Validate next parameter
 * Prevent open redirect attacks
 * Per RFC-001: Only allow relative paths starting with /
 */
function validateNextParam(next: string | null): string | null {
  if (!next) {
    return null;
  }

  // Need start with / (relative path)
  if (!next.startsWith("/")) {
    return null;
  }

  // Disallow protocol-relative URLs
  if (
    next.startsWith("//") ||
    next.startsWith("http://") ||
    next.startsWith("https://")
  ) {
    return null;
  }

  // Disallow URLs with different protocols (e.g., javascript:, data:, etc)
  if (next.includes(":") && !next.startsWith("/")) {
    return null;
  }

  // Valid relative path
  return next;
}

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();
  const { theme, resolvedTheme } = useTheme();
  const { t } = useI18n();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  // Local override to show the plain login form even on a fresh system
  // (an admin is required, but the user has an account elsewhere / via SSO).
  const [showLoginOverride, setShowLoginOverride] = useState(false);
  const [ssoProviders, setSsoProviders] = useState<
    { id: string; display_name: string; type: string }[]
  >([]);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResponse | null>(
    null,
  );
  const [setupStatusChecked, setSetupStatusChecked] = useState(false);

  // Extract error from query params (e.g., ?error=sso_failed)
  const errorParam = searchParams.get("error");
  const [error, setError] = useState(
    errorParam
      ? (t.login.errors[errorParam as keyof typeof t.login.errors] ??
          t.login.authFailed)
      : "",
  );
  // Soft hint shown after a failed login when SSO is configured: an SSO-only
  // account has no local password, so the backend returns a generic
  // "incorrect email or password" (deliberately, to avoid account enumeration).
  // Nudge the user toward the SSO buttons without confirming the account exists.
  const [showSsoHint, setShowSsoHint] = useState(false);
  const [loading, setLoading] = useState(false);

  // Get next parameter for validated redirect
  const nextParam = searchParams.get("next");
  const redirectPath = validateNextParam(nextParam) ?? "/workspace";
  const regularSignupAllowed = canCreateRegularAccount({
    checked: setupStatusChecked,
    status: setupStatus,
  });
  const systemNeedsAdminSetup = setupStatus?.needs_setup === true;
  // When the system has no admin yet, show an inline admin-init form instead of
  // the (unusable) login form unless the user explicitly asks for the login form.
  const adminSetupMode = systemNeedsAdminSetup && !showLoginOverride;

  // Redirect if already authenticated (client-side, post-login)
  useEffect(() => {
    if (isAuthenticated) {
      router.push(redirectPath);
    }
  }, [isAuthenticated, redirectPath, router]);

  // Fetch setup state and SSO providers
  useEffect(() => {
    let cancelled = false;

    void fetchSetupStatus()
      .then((data) => {
        if (cancelled) return;
        setSetupStatus(data);
        if (data.needs_setup) {
          setIsLogin(true);
          setShowLoginOverride(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSetupStatus(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setSetupStatusChecked(true);
        }
      });

    void fetch("/api/v1/auth/providers")
      .then((r) => r.json())
      .then(
        (data: {
          providers: { id: string; display_name: string; type: string }[];
        }) => {
          if (!cancelled) {
            setSsoProviders(data.providers ?? []);
          }
        },
      )
      .catch(() => {
        // Ignore errors; no SSO providers shown
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setShowSsoHint(false);
    setLoading(true);

    // First-run path: create the first admin via /initialize.
    if (adminSetupMode) {
      if (password !== confirmPassword) {
        setError(t.login.passwordsDoNotMatch);
        setLoading(false);
        return;
      }
      try {
        const res = await fetch("/api/v1/auth/initialize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
          credentials: "include",
        });

        if (!res.ok) {
          const data = await res.json();
          if (isSystemAlreadyInitializedError(data)) {
            // An admin was created elsewhere while this tab was open — re-fetch
            // setup status and drop back to the normal login form.
            try {
              const status = await fetchSetupStatus();
              setSetupStatus(status);
            } catch {
              /* ignore — leave as-is, form will re-render on next interaction */
            }
            setShowLoginOverride(true);
            setError("");
          } else {
            const authError = parseAuthError(data);
            setError(authError.message);
          }
          setLoading(false);
          return;
        }

        // /initialize sets the session cookie — go to workspace.
        // Keep loading=true so the spinner stays visible until the page
        // unmounts during navigation — no finally block here.
        void router.push(redirectPath);
      } catch {
        setError(t.login.networkError);
        setLoading(false);
      }
      return;
    }

    if (!isLogin && !regularSignupAllowed) {
      setError(t.login.adminSetupRequiredDescription);
      setLoading(false);
      return;
    }

    try {
      const endpoint = isLogin
        ? "/api/v1/auth/login/local"
        : "/api/v1/auth/register";
      const body = isLogin
        ? `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
        : JSON.stringify({ email, password });

      const headers: HeadersInit = isLogin
        ? { "Content-Type": "application/x-www-form-urlencoded" }
        : { "Content-Type": "application/json" };

      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body,
        credentials: "include", // Important: include HttpOnly cookie
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        // On a failed login with SSO configured, surface a hint pointing at the
        // SSO buttons — the "wrong password" may really mean "this is an SSO account".
        if (isLogin && ssoProviders.length > 0) {
          setShowSsoHint(true);
        }
        setLoading(false);
        return;
      }

      // Both login and register set a cookie — redirect to workspace
      // Keep loading=true so the spinner stays visible until the page
      // unmounts during navigation — no finally block here.
      void router.push(redirectPath);
    } catch {
      setError(t.login.networkError);
      setLoading(false);
    }
  };

  const actualTheme = theme === "system" ? resolvedTheme : theme;

  return (
    <div className="bg-background relative flex min-h-dvh items-center justify-center overflow-x-hidden overflow-y-auto">
      <DeerGridBg
        className="absolute inset-0 z-0"
        color={actualTheme === "dark" ? "white" : "black"}
      />
      <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
        <div className="text-center">
          <h1 className="text-foreground font-serif text-3xl">ModelServer</h1>
          <p className="text-muted-foreground mt-2">
            {adminSetupMode
              ? t.login.adminSetupRequiredTitle
              : isLogin
                ? t.login.signInTitle
                : t.login.createAccountTitle}
          </p>
          {adminSetupMode && (
            <p className="text-muted-foreground mt-1 text-xs">
              {t.login.adminSetupDescription}
            </p>
          )}
        </div>

        {systemNeedsAdminSetup && !adminSetupMode && (
          <div className="border-l-2 border-blue-500 ps-3 text-sm">
            <p className="font-medium">{t.login.adminSetupRequiredTitle}</p>
            <p className="text-muted-foreground mt-1">
              {t.login.adminSetupRequiredDescription}
            </p>
            <Link
              href="/setup"
              className="mt-2 inline-block font-medium text-blue-500 hover:underline"
            >
              {t.login.createAdminAccount}
            </Link>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-2">
          <div className="flex flex-col space-y-1">
            <label htmlFor="email" className="text-sm font-medium">
              {t.login.email}
            </label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setError("");
                setShowSsoHint(false);
              }}
              placeholder={t.login.emailPlaceholder}
              required
            />
          </div>
          <div className="flex flex-col space-y-1">
            <label htmlFor="password" className="text-sm font-medium">
              {t.login.password}
            </label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError("");
                setShowSsoHint(false);
              }}
              placeholder={t.login.passwordPlaceholder}
              required
              minLength={adminSetupMode || !isLogin ? 8 : 6}
            />
          </div>
          {adminSetupMode && (
            <div className="flex flex-col space-y-1">
              <label htmlFor="confirmPassword" className="text-sm font-medium">
                {t.login.confirmPassword}
              </label>
              <Input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => {
                  setConfirmPassword(e.target.value);
                  setError("");
                  setShowSsoHint(false);
                }}
                placeholder={t.login.confirmPasswordPlaceholder}
                required
                minLength={8}
              />
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500"
            >
              <AlertCircle className="size-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading && <Loader2 className="animate-spin" />}
            {loading
              ? adminSetupMode
                ? t.login.creatingAdmin
                : isLogin
                  ? t.login.signingIn
                  : t.login.creatingAccount
              : adminSetupMode
                ? t.login.createAdminAccount
                : isLogin
                  ? t.login.signIn
                  : t.login.createAccount}
          </Button>
        </form>

        {ssoProviders.length > 0 && (
          <div className="space-y-2">
            {(isLogin || adminSetupMode) && (
              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-background text-muted-foreground px-2">
                    {t.login.orContinueWith}
                  </span>
                </div>
              </div>
            )}
            {showSsoHint && (
              <p className="text-muted-foreground text-center text-sm">
                {t.login.ssoHint}
              </p>
            )}
            {ssoProviders.map((provider) => (
              <Button
                key={provider.id}
                type="button"
                variant="outline"
                className="w-full"
                disabled={loading}
                onClick={() => {
                  window.location.href = `/api/v1/auth/oauth/${provider.id}?next=${encodeURIComponent(redirectPath)}`;
                }}
              >
                {t.login.continueWith(provider.display_name)}
              </Button>
            ))}
          </div>
        )}

        {adminSetupMode && (
          <div className="text-center text-sm">
            <button
              type="button"
              onClick={() => {
                setShowLoginOverride(true);
                setError("");
                setShowSsoHint(false);
              }}
              className="text-blue-500 hover:underline"
            >
              {t.login.haveAccountSignIn}
            </button>
          </div>
        )}

        {!adminSetupMode && regularSignupAllowed && (
          <div className="text-center text-sm">
            <button
              type="button"
              onClick={() => {
                setIsLogin(!isLogin);
                setError("");
                setShowSsoHint(false);
              }}
              className="text-blue-500 hover:underline"
            >
              {isLogin ? t.login.noAccountSignUp : t.login.haveAccountSignIn}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
