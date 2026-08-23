import { Cpu, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import AddModelModal from "@/components/models/AddModelModal";
import EditRegisteredModelModal from "@/components/models/EditRegisteredModelModal";
import ModelRow from "@/components/models/ModelRow";
import RegisterModelModal from "@/components/models/RegisterModelModal";
import Badge from "@/components/ui/Badge";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Card from "@/components/ui/Card";
import CurrencyToggle from "@/components/ui/CurrencyToggle";
import EmptyState from "@/components/ui/EmptyState";
import Select from "@/components/ui/Select";
import Spinner from "@/components/ui/Spinner";
import { useAccounts } from "@/hooks/useAccounts";
import { useBreakdownByAccount, useBreakdownByDeployment, useUsdInrRate } from "@/hooks/useDashboard";
import { useEstimateCurrency } from "@/hooks/useEstimateCurrency";
import { useModels } from "@/hooks/useModels";
import { useDeleteRegisteredModel, useRegisteredModels } from "@/hooks/useRegisteredModels";
import { formatCurrency } from "@/lib/format";
import { RegisteredModel } from "@/types";

const ALL = "all";

export default function ModelsPage() {
  const { data: models, isLoading, isError: modelsError } = useModels();
  const { data: accounts } = useAccounts();
  const { data: registeredModels, isLoading: isRegistryLoading, isError: registryLoadError } = useRegisteredModels();
  const { data: usageByDeployment, isLoading: usageLoading, isError: usageError } = useBreakdownByDeployment("30d");
  const { data: usageByAccount } = useBreakdownByAccount("30d");
  const deleteRegisteredModel = useDeleteRegisteredModel();
  const { currency: estimateCurrency, setCurrency: setEstimateCurrency } = useEstimateCurrency();
  const fx = useUsdInrRate();
  const usdInr = fx.data?.usd_inr ?? 87;

  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [isRegisterModalOpen, setIsRegisterModalOpen] = useState(false);
  const [editingRegistered, setEditingRegistered] = useState<RegisteredModel | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [deletingRegisteredId, setDeletingRegisteredId] = useState<number | null>(null);
  const [accountFilter, setAccountFilter] = useState<string>(ALL);
  const [search, setSearch] = useState("");

  const usageById = new Map((usageByDeployment ?? []).map((item) => [item.id, item]));
  const newApiByAccountId = new Map((accounts ?? []).map((account) => [account.id, account.new_api_cost_usd]));
  const actualByAccountId = new Map((usageByAccount ?? []).map((item) => [item.id, item]));
  const firstRowByAccount = new Set<number>();

  const filteredModels = useMemo(() => {
    return (models ?? []).filter((model) => {
      if (!model.model_name || !model.deployment_name) return false;
      if (accountFilter !== ALL && model.provider_account_id !== Number(accountFilter)) return false;
      if (search.trim()) {
        const needle = search.trim().toLowerCase();
        return (
          model.model_name.toLowerCase().includes(needle) ||
          model.deployment_name.toLowerCase().includes(needle) ||
          (model.provider_account_name ?? "").toLowerCase().includes(needle)
        );
      }
      return true;
    });
  }, [models, accountFilter, search]);

  async function handleDeleteRegistered(model: RegisteredModel) {
    setRegistryError(null);
    setDeletingRegisteredId(model.id);
    try {
      await deleteRegisteredModel.mutateAsync(model.id);
    } catch (err: any) {
      setRegistryError(err?.response?.data?.detail ?? "Could not delete this model.");
    } finally {
      setDeletingRegisteredId(null);
    }
  }

  return (
    <div className="flex flex-col gap-5 sm:gap-6">
      <div className="min-w-0">
        <h1 className="gradient-title text-2xl font-semibold tracking-tight">Models</h1>
        <p className="mt-1 text-sm text-gray-500">Register a model once with its price, then link it to any account's deployment</p>
      </div>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm font-semibold text-gray-200">Model registry</h2>
          <Button onClick={() => setIsRegisterModalOpen(true)} className="w-full text-xs sm:w-auto">
            <Plus size={14} /> Register model
          </Button>
        </div>

        {registryError && <Banner tone="error">{registryError}</Banner>}
        {registryLoadError && <Banner tone="error">Could not load the model registry. Try refreshing the page.</Banner>}

        {isRegistryLoading ? (
          <Spinner />
        ) : registeredModels && registeredModels.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {registeredModels.map((model) => (
              <Card key={model.id} className="flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-gray-100">{model.name}</p>
                  <Badge tone="neutral">
                    {model.deployments_count} deployment{model.deployments_count === 1 ? "" : "s"}
                  </Badge>
                </div>
                <p className="min-w-0 break-words text-xs text-gray-500">
                  {formatCurrency(model.input_price_per_million, model.currency)} in ·{" "}
                  {formatCurrency(model.cached_input_price_per_million, model.currency)} cached ·{" "}
                  {formatCurrency(model.output_price_per_million, model.currency)} out{" "}
                  <span className="text-gray-600">/ 1M tokens</span>
                </p>
                <div className="mt-1 flex gap-2">
                  <Button variant="secondary" className="px-2.5 py-1.5 text-xs" onClick={() => setEditingRegistered(model)}>
                    <Pencil size={13} /> Edit price
                  </Button>
                  <Button
                    variant="danger"
                    className="px-2.5 py-1.5"
                    onClick={() => handleDeleteRegistered(model)}
                    isLoading={deletingRegisteredId === model.id}
                    disabled={model.deployments_count > 0}
                    title={model.deployments_count > 0 ? "Remove its deployments first" : "Delete"}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Cpu size={28} className="text-gray-600" />}
            title="No models registered yet"
            description="Register a model with a name and price once - every account's deployment of it can then link here."
            action={<Button onClick={() => setIsRegisterModalOpen(true)}>Register model</Button>}
          />
        )}
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm font-semibold text-gray-200">Deployments</h2>
          <Button onClick={() => setIsAddModalOpen(true)} className="w-full sm:w-auto">
            <Plus size={16} /> Add deployment
          </Button>
        </div>

        {models && models.length > 0 && (
          <Card>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex min-w-0 flex-col gap-1.5">
                <label className="text-xs font-medium text-gray-400">Search</label>
                <div className="relative">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Model, deployment or account"
                    className="w-full rounded-lg border border-surface-border bg-surface py-2 pl-8 pr-3 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent"
                  />
                </div>
              </div>
              <Select label="Account" value={accountFilter} onChange={(e) => setAccountFilter(e.target.value)}>
                <option value={ALL}>All accounts</option>
                {(accounts ?? [])
                  .filter((account) => Boolean(account.name?.trim()))
                  .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: "base" }))
                  .map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name}
                    </option>
                  ))}
              </Select>
            </div>
          </Card>
        )}

        {modelsError && <Banner tone="error">Could not load deployments. Try refreshing the page.</Banner>}
        {usageError && <Banner tone="error">Could not load 30-day usage for deployments.</Banner>}

        {isLoading ? (
          <Spinner />
        ) : models && models.length > 0 ? (
          filteredModels.length > 0 ? (
            <Card className="overflow-x-auto p-0 sm:p-5">
              <table className="w-full min-w-[920px] text-left">
                <thead>
                  <tr className="border-b border-surface-border text-xs uppercase tracking-wide text-gray-500">
                    <th className="py-3 pl-4 pr-4 font-medium sm:pl-0">Model</th>
                    <th className="py-3 pr-4 font-medium">Account</th>
                    <th className="py-3 pr-4 font-medium">Tokens (30d)</th>
                    <th className="py-3 pr-4 font-medium">
                      <div className="flex items-center gap-2 normal-case">
                        <span className="uppercase tracking-wide">Est. cost (30d)</span>
                        <CurrencyToggle value={estimateCurrency} onChange={setEstimateCurrency} />
                      </div>
                    </th>
                    <th
                      className="py-3 pr-4 font-medium"
                      title="Azure billed amount for the account (30d). Shown once per account, not per deployment."
                    >
                      Actual (account, 30d)
                    </th>
                    <th
                      className="py-3 pr-4 font-medium"
                      title="Gateway lifetime quota for the account. Shown once per account, not per deployment."
                    >
                      NewAPI (account)
                    </th>
                    <th className="py-3 pr-4 font-medium">Status</th>
                    <th className="py-3 pr-4 text-right font-medium sm:pr-0">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredModels.map((model) => {
                    const showAccountCosts = !firstRowByAccount.has(model.provider_account_id);
                    firstRowByAccount.add(model.provider_account_id);
                    const accountActual = actualByAccountId.get(model.provider_account_id);
                    return (
                      <ModelRow
                        key={model.id}
                        model={model}
                        usage={usageById.get(model.id)}
                        usageLoading={usageLoading}
                        estimateCurrency={estimateCurrency}
                        usdInr={usdInr}
                        showAccountCosts={showAccountCosts}
                        newApiCost={newApiByAccountId.get(model.provider_account_id) ?? null}
                        actualCost={accountActual?.actual_cost ?? null}
                        actualCostCurrency={accountActual?.actual_cost_currency ?? null}
                      />
                    );
                  })}
                </tbody>
              </table>
            </Card>
          ) : (
            <EmptyState
              icon={<Search size={28} className="text-gray-600" />}
              title="No deployments match your filters"
              description="Try a different search term or account filter."
            />
          )
        ) : (
          <EmptyState
            icon={<Cpu size={28} className="text-gray-600" />}
            title="No deployments monitored yet"
            description="Register a model above, then add an account's deployment and link it to that model."
            action={<Button onClick={() => setIsAddModalOpen(true)}>Add deployment</Button>}
          />
        )}
      </section>

      <AddModelModal isOpen={isAddModalOpen} onClose={() => setIsAddModalOpen(false)} />
      <RegisterModelModal isOpen={isRegisterModalOpen} onClose={() => setIsRegisterModalOpen(false)} />
      <EditRegisteredModelModal model={editingRegistered} onClose={() => setEditingRegistered(null)} />
    </div>
  );
}
