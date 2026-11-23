import { SignedIn, SignedOut, SignInButton, SignUpButton, UserButton } from "@clerk/clerk-react";
import { useEffect, useState } from "react";
import ContractList from "./components/ContractList";
import ContractView from "./components/ContractView";

// Tiny hash router: "#/" = list, "#/contracts/<id>" = one contract.
function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const hash = useHashRoute();
  const match = hash.match(/^#\/contracts\/([0-9a-f-]{36})/i);

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          <span className="brand-mark">§</span> Contract Review Assistant
        </a>
        <SignedIn>
          <UserButton />
        </SignedIn>
      </header>

      <SignedOut>
        <main className="landing">
          <h1>Check a contract against a fixed checklist.</h1>
          <p>
            Upload a PDF, DOCX or TXT contract. For each item (parties, term, payment, termination, liability,
            confidentiality, governing law, auto-renewal) you get a status, a one-line summary and the exact clause
            it came from. Answers come only from your contract.
          </p>
          <div className="landing-actions">
            <SignInButton mode="modal">
              <button className="btn primary">Sign in</button>
            </SignInButton>
            <SignUpButton mode="modal">
              <button className="btn">Create account</button>
            </SignUpButton>
          </div>
        </main>
      </SignedOut>

      <SignedIn>
        <main className="content">{match ? <ContractView id={match[1]} /> : <ContractList />}</main>
      </SignedIn>
    </div>
  );
}
