import { redirect } from "next/navigation";

export default function LegacyAICostsPage() {
  redirect("/agent-operations?tab=costs");
}
