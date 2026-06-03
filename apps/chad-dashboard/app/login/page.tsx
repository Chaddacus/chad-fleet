import { LoginForm } from '@/features/auth/login-ui';

// Thin shell route — mounts the auth feature's login form.
export default function LoginPage({
  searchParams,
}: {
  searchParams: { next?: string };
}) {
  return <LoginForm next={searchParams.next ?? '/'} />;
}
