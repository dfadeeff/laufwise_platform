import { SignIn } from "@clerk/nextjs";

// Dedicated sign-in route (Clerk's recommended production pattern). The catch-all
// segment lets Clerk own the sub-paths (factor-one, SSO callback, etc.). Public —
// the middleware only protects /studio and /runs.
export default function SignInPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-5 py-16">
      <SignIn signUpUrl="/sign-up" fallbackRedirectUrl="/studio" />
    </div>
  );
}
