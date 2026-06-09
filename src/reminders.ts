import { needsReminderBodyMetrics, lastMetricDate } from "./metrics";

export interface Reminder {
  type: string;
  message: string;
  priority: "alta" | "media" | "baixa";
}

export async function checkAllReminders(): Promise<Reminder[]> {
  const reminders: Reminder[] = [];

  if (await needsReminderBodyMetrics()) {
    const last = await lastMetricDate();
    const msg = last
      ? `Ultimo registro de peso/cintura foi em ${last.toLocaleDateString("pt-PT")} — ja faz mais de 7 dias. Hora de medir!`
      : "Nenhum registro de peso ou cintura encontrado. Registre suas metricas corporais!";
    reminders.push({ type: "metricas_corporais", message: msg, priority: "alta" });
  }

  return reminders;
}
