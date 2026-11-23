import { ClerkProvider } from "@clerk/clerk-react";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;
const root = ReactDOM.createRoot(document.getElementById("root")!);

if (!publishableKey || publishableKey.includes("xxx")) {
  root.render(
    <div className="setup-error">
      <h1>Clerk key missing</h1>
      <p>
        Set <code>VITE_CLERK_PUBLISHABLE_KEY</code> in the <code>.env</code> file at the project root, then restart.
        See the README, step "Create the Clerk application".
      </p>
    </div>,
  );
} else {
  root.render(
    <React.StrictMode>
      <ClerkProvider publishableKey={publishableKey} afterSignOutUrl="/">
        <App />
      </ClerkProvider>
    </React.StrictMode>,
  );
}
