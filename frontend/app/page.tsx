"use client";

import { useState, useEffect } from "react";

export default function Home() {
  // one piece of state per form field (controlled inputs)
  const [endpointId, setEndpointId] = useState("");
  const [eventType, setEventType] = useState("");
  const [payload, setPayload] = useState('{"order_id": 123}');

  // state for response / error

  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  // async function handleSubmit to handle the defualt behavior of the react forms

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault(); //stop the default full page reload
    setError(null)
    setResult(null)
  
    // the textarea is string; the API wants a JSON object -> parse it
    let parsedPayload;
    try {
      parsedPayload = JSON.parse(payload); // the payload that will parse now will be a JSON object
    } catch {
      setError("Payload must be valid JSON");
      return;
    }

    // fetch the events from the server -> using the fetch command

    try {
      const res = await fetch("http://localhost:8000/events", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          endpoint_id: Number(endpointId), // input gives a string -> number
          event_type: eventType,
          payload: parsedPayload,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(`Request failed (${res.status}): ${JSON.stringify(body)}`);
        return;
      }
      setResult(await res.json()); // the created event: id + status "pending"
    } catch {
      setError("Could not reach the API - is the backend running?");
    }

    

    
  }
  // WATCH: poll this event's status until it's no longer pending
  useEffect(()=> {
    if (!result || result.status !== "pending") return // nothing to watch

    const id = result.id;
    const interval = setInterval(async () => {
      const res = await fetch(`http://localhost:8000/events/${id}`);
      if(res.ok) setResult(await res.json()); // update the state - re-render 
    }, 1500);

    return () => clearInterval(interval); // cleanup: stop the timer
  }, [result?.id, result?.status]);
 return (
    <main className="max-w-lg mx-auto p-8">
      <h1 className="text-2xl font-bold mb-6">Emit an Event</h1>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <input type="number" placeholder="Endpoint ID" value={endpointId}
          onChange={(e) => setEndpointId(e.target.value)} className="border rounded px-3 py-2" />
        <input type="text" placeholder="Event type (e.g. order.created)" value={eventType}
          onChange={(e) => setEventType(e.target.value)} className="border rounded px-3 py-2" />
        <textarea placeholder="Payload (JSON)" value={payload}
          onChange={(e) => setPayload(e.target.value)} rows={4}
          className="border rounded px-3 py-2 font-mono text-sm" />
        <button type="submit" className="bg-black text-white rounded px-4 py-2">Emit event</button>
      </form>

      {error && <p className="text-red-600 mt-4">{error}</p>}

      {result && (
        <div className="mt-6 border rounded p-4">
          <p className="text-sm text-zinc-500">Event #{result.id} · {result.event_type}</p>
          <p className="mt-1">
            Status:{" "}
            <span className={
              result.status === "delivered" ? "text-green-600 font-semibold"
              : result.status === "dead" ? "text-red-600 font-semibold"
              : "text-amber-600 font-semibold"
            }>
              {result.status}{result.status === "pending" && " ⏳"}
            </span>
          </p>
        </div>
      )}
    </main>
  );
}
