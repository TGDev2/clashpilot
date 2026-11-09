import { fr } from "./i18n/fr";

export function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>{fr.app.title}</h1>
        <p>{fr.app.tagline}</p>
      </header>
      <main className="app-main">
        <p>{fr.app.placeholder}</p>
      </main>
      <footer className="app-footer">
        <small>{fr.footer.fanContentPolicy}</small>
      </footer>
    </div>
  );
}
