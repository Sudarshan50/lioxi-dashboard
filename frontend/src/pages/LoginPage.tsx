import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import Input from "@/components/ui/Input";
import { useAuth } from "@/context/AuthContext";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      await login(username, password);
      navigate("/");
    } catch {
      setError("Invalid username or password");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="app-aurora flex min-h-screen items-center justify-center bg-surface px-4">
      <Card className="w-full max-w-sm animate-fade-up">
        <div className="mb-6 text-center">
          <img src="/lioxi-icon.png" alt="" className="mx-auto mb-4 h-12 w-12 rounded-2xl shadow-glow-sm" />
          <h1 className="gradient-title text-xl font-semibold">Lioxi</h1>
          <p className="mt-1 text-sm text-gray-500">Sign in to the command center</p>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Input label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
          <Input label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          {error && <p className="break-words text-xs text-red-400">{error}</p>}
          <Button type="submit" isLoading={isLoading} className="mt-2 w-full">
            Sign in
          </Button>
        </form>
      </Card>
    </div>
  );
}
