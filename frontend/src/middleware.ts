import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// The landing page is public; the Studio and operator console require a signed-in user
// within an organization (org = tenant, ADR-0003 D2). Route protection is enforced here so
// no app surface renders tenant data without an authenticated principal.
const isProtected = createRouteMatcher(["/studio(.*)", "/runs(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (isProtected(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next internals and static files, unless found in search params
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes and Clerk's auto-proxy path
    "/(api|trpc)(.*)",
    "/__clerk/:path*",
  ],
};