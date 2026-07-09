import { SignUp } from "@clerk/nextjs";

// Dedicated sign-up route (Clerk's recommended production pattern). The catch-all
// segment lets Clerk own the sub-paths (verify-email-address, continue, etc.). Public.
export default function SignUpPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-5 py-16">
      <SignUp signInUrl="/sign-in" fallbackRedirectUrl="/studio" />
    </div>
  );
}
