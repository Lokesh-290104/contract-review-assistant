import { ContractStatus, FindingStatus } from "../api";

const LABELS: Record<string, string> = {
  processing: "Processing",
  reviewed: "Reviewed",
  failed: "Failed",
  found: "Found",
  not_found: "Not found",
  unclear: "Unclear",
};

export default function StatusBadge({ status }: { status: ContractStatus | FindingStatus }) {
  return (
    <span className={`badge badge-${status}`}>
      {status === "processing" && <span className="spinner" aria-hidden />}
      {LABELS[status] ?? status}
    </span>
  );
}
