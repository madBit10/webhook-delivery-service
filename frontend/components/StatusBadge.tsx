import { WebhookEvent } from "@/lib/api";

export default function StatusBadge({status}: {status: WebhookEvent["status"]}) {
    const color = status === "delivered" ? "text-green-600" : status === "dead" ? "text-red-600" : "text-amber-600";

    return (
        <span className={`font-semibold ${color}`}>
            {status}{status == "pending" && " ⏳"}
        </span>
    );
}