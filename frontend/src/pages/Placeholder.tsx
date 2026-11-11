import { PageTitle } from "../components/ui";
import { fr } from "../i18n/fr";

export function Placeholder({ title }: { title: string }) {
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <p className="text-mist">{fr.soon.text}</p>
    </>
  );
}
