import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { AccountGroup } from "@/types";
import { downloadDashboardCsv, readApiError } from "@/lib/downloadCsv";

const ALL = "all";

const RANGE_OPTIONS = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
];

interface ExportCsvModalProps {
  isOpen: boolean;
  onClose: () => void;
  groups: AccountGroup[];
  defaultRange: string;
  defaultGroupId: string;
}

export default function ExportCsvModal({ isOpen, onClose, groups, defaultRange, defaultGroupId }: ExportCsvModalProps) {
  const [range, setRange] = useState(defaultRange);
  const [groupId, setGroupId] = useState(defaultGroupId);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setRange(defaultRange);
    setGroupId(defaultGroupId);
    setError(null);
  }, [isOpen, defaultRange, defaultGroupId]);

  async function handleExport() {
    setError(null);
    setIsExporting(true);
    try {
      await downloadDashboardCsv({
        range,
        groupId: groupId === ALL ? null : Number(groupId),
      });
      onClose();
    } catch (err) {
      setError(await readApiError(err, "Could not export CSV."));
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <Modal title="Export usage CSV" isOpen={isOpen} onClose={onClose} widthClassName="max-w-md">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-gray-400">
          Account summary (O1/O2 lifetime NewAPI, Azure credits, in-range tokens/costs) plus one row per deployment.
        </p>
        <Select label="Group" value={groupId} onChange={(e) => setGroupId(e.target.value)}>
          <option value={ALL}>All accounts</option>
          {groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.name} ({group.accounts.length})
            </option>
          ))}
        </Select>
        <Select label="Time range" value={range} onChange={(e) => setRange(e.target.value)}>
          {RANGE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={isExporting}>
            Cancel
          </Button>
          <Button onClick={handleExport} isLoading={isExporting}>
            Download CSV
          </Button>
        </div>
      </div>
    </Modal>
  );
}
