"use client";

import { useState, useEffect } from "react";
import { API_BASE, WebhookEvent } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

export default function EventsPage() {
  const [events, setEvents] = useState<WebhookEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/events?limit=100`);
        if (!res.ok) {
          setError(`Failed to load events (${res.status})`);
          return;
        }
        setEvents(await res.json());
      } catch {
        setError("Could not reach the API — is the backend running?");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []); // empty deps → run once on mount

  if (loading) return <main className="p-8">Loading…</main>;
  if (error) return <main className="p-8 text-red-600">{error}</main>;

  return (
    <main className="max-w-4xl mx-auto p-8">
      <h1 className="text-2xl font-bold mb-6">Events</h1>

      {events.length === 0 ? (
        <p className="text-zinc-500">No events yet.</p>
      ) : (
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b text-sm text-zinc-500">
              <th className="py-2 pr-4">ID</th>
              <th className="py-2 pr-4">Type</th>
              <th className="py-2 pr-4">Endpoint</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => (
              <tr key={ev.id} className="border-b text-sm">
                <td className="py-2 pr-4">{ev.id}</td>
                <td className="py-2 pr-4">{ev.event_type}</td>
                <td className="py-2 pr-4">{ev.endpoint_id}</td>
                <td className="py-2 pr-4"><StatusBadge status={ev.status} /></td>
                <td className="py-2 text-zinc-500">
                  {new Date(ev.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}