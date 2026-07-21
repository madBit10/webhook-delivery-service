"use client";

import { useState, useEffect } from "react";
import { API_BASE, WebhookEvent } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

export default function DlqPage() {
  const [dead, setDead] = useState<WebhookEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [replaying, setReplaying] = useState(false);

  // named so both mount AND replay can call it
  async function loadDead() {
    try {
      const res = await fetch(`${API_BASE}/events?limit=100`);
      if (!res.ok) { setError(`Failed to load (${res.status})`); return; }
      const all: WebhookEvent[] = await res.json();
      setDead(all.filter((ev) => ev.status === "dead"));   // Option A: client-side filter
    } catch {
      setError("Could not reach the API — is the backend running?");
    }
  }

  useEffect(() => { loadDead(); }, []);   // load once on mount

  async function handleReplay() {
    setReplaying(true);
    try {
      await fetch(`${API_BASE}/dlq/replay`, { method: "POST" });
      // replay re-queues them; give the worker a moment, then refresh
      setTimeout(loadDead, 1500);
    } finally {
      setReplaying(false);
    }
  }

  return (
    <main className="max-w-4xl mx-auto p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Dead-Letter Queue</h1>
        <button
          onClick={handleReplay}
          disabled={replaying || dead.length === 0}
          className="bg-black text-white rounded px-4 py-2 disabled:opacity-40"
        >
          {replaying ? "Replaying…" : "Replay all"}
        </button>
      </div>

      {error && <p className="text-red-600">{error}</p>}

      {dead.length === 0 ? (
        <p className="text-zinc-500">No dead events. 🎉</p>
      ) : (
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b text-sm text-zinc-500">
              <th className="py-2 pr-4">ID</th>
              <th className="py-2 pr-4">Type</th>
              <th className="py-2 pr-4">Endpoint</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {dead.map((ev) => (
              <tr key={ev.id} className="border-b text-sm">
                <td className="py-2 pr-4">{ev.id}</td>
                <td className="py-2 pr-4">{ev.event_type}</td>
                <td className="py-2 pr-4">{ev.endpoint_id}</td>
                <td className="py-2"><StatusBadge status={ev.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}