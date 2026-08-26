import { Cpu, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import AccountDeploymentGroup from "@/components/models/AccountDeploymentGroup";
import AddModelModal from "@/components/models/AddModelModal";
import EditRegisteredModelModal from "@/components/models/EditRegisteredModelModal";
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
import {
  actualSpendUsd,
  combinedSpendUsd,
  compareOptionalNumbers,
  compareOptionalText,
  consumedPercent,
  creditLeftRatio,
  creditOutstandingUsd,
  creditRemainingUsd,
  finiteNumber,
  gatewayRank,
  portalSpendUsd,
} from "@/lib/accountSort";
import { formatCurrency } from "@/lib/format";
import { amountPayableUsd } from "@/lib/payable";
import { matchesOwner, ownerLabel, uniqueOwners, UNTAGGED_OWNER } from "@/lib/ownerTag";
import { Account, BreakdownItem, MonitoredModel, RegisteredModel } from "@/types";

const ALL = "all";

type ModelSort =
  | "account"
  | "account-desc"
  | "credits-left"
  | "credits-left-desc"
  | "consumed"
  | "outstanding"
  | "newapi-spend"
  | "payable"
  | "newapi-o1"
  | "newapi-o2"
  | "actual"
  | "tokens"
  | "estimated"
  | "deployments"
  | "gateway"
  | "owner";

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
  const [ownerFilter, setOwnerFilter] = useState<string>(ALL);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ModelSort>("account");

  const usageById = useMemo(
    () => new Map((usageByDeployment ?? []).map((item) => [item.id, item])),
    [usageByDeployment]
  );
  const accountById = useMemo(() => new Map((accounts ?? []).map((account) => [account.id, account])), [accounts]);
  const actualByAccountId = useMemo(
    () => new Map((usageByAccount ?? []).map((item) => [item.id, item])),
    [usageByAccount]
  );

  const owner = ownerFilter === ALL ? null : ownerFilter;
  const ownerOptions = useMemo(() => uniqueOwners(accounts ?? []), [accounts]);

  const filteredModels = useMemo(() => {
    return (models ?? []).filter((model) => {
      if (!model.model_name || !model.deployment_name) return false;
      if (accountFilter !== ALL && model.provider_account_id !== Number(accountFilter)) return false;
      const account = accountById.get(model.provider_account_id);
      if (owner && !matchesOwner(account?.owner_tag, owner)) return false;
      if (search.trim()) {
        const needle = search.trim().toLowerCase();
        return (
          model.model_name.toLowerCase().includes(needle) ||
          model.deployment_name.toLowerCase().includes(needle) ||
          (model.provider_account_name ?? "").toLowerCase().includes(needle) ||
          (account?.owner_tag ?? "").toLowerCase().includes(needle) ||
          (account?.new_api_name ?? "").toLowerCase().includes(needle) ||
          (account?.resource_name ?? "").toLowerCase().includes(needle)
        );
      }
      return true;
    });
  }, [accountById, accountFilter, models, owner, search]);

  const deploymentGroups = useMemo(() => {
    const byAccount = new Map<number, typeof filteredModels>();
    for (const model of filteredModels) {
      const rows = byAccount.get(model.provider_account_id) ?? [];
      rows.push(model);
      byAccount.set(model.provider_account_id, rows);
    }
    return [...byAccount.entries()]
      .map(([accountId, deployments]) => {
        const sortedDeployments = [...deployments].sort((left, right) =>
          compareDeployments(left, right, usageById, sort)
        );
        return {
          accountId,
          account: accountById.get(accountId),
          accountName: deployments[0]?.provider_account_name || accountById.get(accountId)?.name || "Account",
          deployments: sortedDeployments,
          actual: actualByAccountId.get(accountId),
        };
      })
      .sort((left, right) => compareAccountGroups(left, right, usageById, sort, usdInr));
  }, [accountById, actualByAccountId, filteredModels, sort, usdInr, usageById]);

  const ownerTotals = useMemo(() => {
    const spend = deploymentGroups.reduce((sum, group) => sum + (group.account?.new_api_cost_usd || 0), 0);
    return {
      spend,
      payable: amountPayableUsd(spend),
      accounts: deploymentGroups.length,
      deployments: filteredModels.length,
    };
  }, [deploymentGroups, filteredModels.length]);

  const deploymentGroupsByTag = useMemo(() => {
    const groups = new Map<string, typeof deploymentGroups>();
    for (const group of deploymentGroups) {
      const tag = ownerLabel(group.account?.owner_tag);
      const rows = groups.get(tag) ?? [];
      rows.push(group);
      groups.set(tag, rows);
    }
    return [...groups.entries()].sort((left, right) => left[0].localeCompare(right[0], undefined, { sensitivity: "base" }));
  }, [deploymentGroups]);

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
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-gray-200">Deployments</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              Grouped by tag, then account · Azure credits are shared · NewAPI shows O1 and O2 wherever the account is mapped
            </p>
          </div>
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
            <CurrencyToggle value={estimateCurrency} onChange={setEstimateCurrency} />
            <Button onClick={() => setIsAddModalOpen(true)} className="w-full sm:w-auto">
              <Plus size={16} /> Add deployment
            </Button>
          </div>
        </div>

        {models && models.length > 0 && (
          <div className="rounded-2xl border border-white/[0.06] bg-surface-raised/80 bg-card-sheen p-4 shadow-card backdrop-blur-sm sm:p-5">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_11rem_12rem_13.5rem] xl:items-end">
              <div className="flex min-w-0 flex-col gap-1.5 md:col-span-2 xl:col-span-1">
                <label htmlFor="model-search" className="text-xs font-medium text-gray-400">
                  Search
                </label>
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                  <input
                    id="model-search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Model, deployment, account, channel, or tag"
                    className="w-full rounded-lg border border-surface-border bg-black/30 py-2 pl-8 pr-3 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-600 focus:border-accent"
                  />
                </div>
              </div>
              <Select
                label="Tag"
                value={ownerFilter}
                onChange={(e) => {
                  setOwnerFilter(e.target.value);
                  setAccountFilter(ALL);
                }}
              >
                <option value={ALL}>All tags</option>
                {ownerOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
                {(accounts ?? []).some((account) => !(account.owner_tag ?? "").trim()) && (
                  <option value={UNTAGGED_OWNER}>Untagged</option>
                )}
              </Select>
              <Select label="Account" value={accountFilter} onChange={(e) => setAccountFilter(e.target.value)}>
                <option value={ALL}>All accounts</option>
                {(accounts ?? [])
                  .filter((account) => Boolean(account.name?.trim()) && matchesOwner(account.owner_tag, owner))
                  .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: "base" }))
                  .map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name}
                    </option>
                  ))}
              </Select>
              <Select label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as ModelSort)}>
                <option value="account">Account A–Z</option>
                <option value="account-desc">Account Z–A</option>
                <option value="credits-left">Credits left (low)</option>
                <option value="credits-left-desc">Credits left (high)</option>
                <option value="consumed">Consumed %</option>
                <option value="outstanding">Outstanding</option>
                <option value="newapi-spend">NewAPI spend</option>
                <option value="payable">Amount payable</option>
                <option value="newapi-o1">O1 spend</option>
                <option value="newapi-o2">O2 spend</option>
                <option value="actual">Actual billed (30d)</option>
                <option value="tokens">Tokens (30d)</option>
                <option value="estimated">Est. cost (30d)</option>
                <option value="deployments">Deployment count</option>
                <option value="gateway">Gateway status</option>
                <option value="owner">Tag</option>
              </Select>
            </div>
            {owner && (
              <p className="mt-3 text-xs text-gray-500">
                Combined for {owner === UNTAGGED_OWNER ? "untagged" : owner}:{" "}
                <span className="tabular-nums text-violet-300">{formatCurrency(ownerTotals.spend, "USD")}</span> spend ·{" "}
                <span className="tabular-nums text-amber-200">{formatCurrency(ownerTotals.payable, "USD")}</span> payable ·{" "}
                <span className="tabular-nums text-gray-300">{ownerTotals.accounts}</span> account
                {ownerTotals.accounts === 1 ? "" : "s"} ·{" "}
                <span className="tabular-nums text-gray-300">{ownerTotals.deployments}</span> deployment
                {ownerTotals.deployments === 1 ? "" : "s"}
              </p>
            )}
          </div>
        )}

        {modelsError && <Banner tone="error">Could not load deployments. Try refreshing the page.</Banner>}
        {usageError && <Banner tone="error">Could not load 30-day usage for deployments.</Banner>}

        {isLoading ? (
          <Spinner />
        ) : models && models.length > 0 ? (
          filteredModels.length > 0 ? (
            <div className="flex flex-col gap-6">
              {deploymentGroupsByTag.map(([tag, rows]) => {
                const spend = rows.reduce((sum, group) => sum + (group.account?.new_api_cost_usd || 0), 0);
                return (
                  <div key={tag} className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <h3 className="text-sm font-semibold text-gray-200">{tag}</h3>
                      <p className="text-xs text-gray-500">
                        <span className="tabular-nums text-violet-300">{formatCurrency(spend, "USD")}</span> spend ·{" "}
                        <span className="tabular-nums text-amber-200">{formatCurrency(amountPayableUsd(spend), "USD")}</span>{" "}
                        payable · {rows.length} account{rows.length === 1 ? "" : "s"}
                      </p>
                    </div>
                    <div className="flex flex-col gap-4">
                      {rows.map((group) => (
                        <AccountDeploymentGroup
                          key={group.accountId}
                          account={group.account}
                          accountName={group.accountName}
                          deployments={group.deployments}
                          usageById={usageById}
                          usageLoading={usageLoading}
                          estimateCurrency={estimateCurrency}
                          usdInr={usdInr}
                          actualCost={group.actual?.actual_cost ?? null}
                          actualCostCurrency={group.actual?.actual_cost_currency ?? null}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
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

interface DeploymentGroup {
  accountId: number;
  account?: Account;
  accountName: string;
  deployments: MonitoredModel[];
  actual?: BreakdownItem;
}

function compareAccountGroups(
  left: DeploymentGroup,
  right: DeploymentGroup,
  usageById: Map<number, BreakdownItem>,
  sort: ModelSort,
  usdInr: number
): number {
  const name = compareOptionalText(left.accountName, right.accountName);
  let primary = 0;
  if (sort === "account") primary = name;
  else if (sort === "account-desc") primary = -name;
  else if (sort === "credits-left") {
    primary = compareOptionalNumbers(creditLeftRatio(left.account), creditLeftRatio(right.account), "asc");
  } else if (sort === "credits-left-desc") {
    primary = compareOptionalNumbers(creditLeftRatio(left.account), creditLeftRatio(right.account), "desc");
  } else if (sort === "consumed") {
    primary = compareOptionalNumbers(consumedPercent(left.account), consumedPercent(right.account), "desc");
  } else if (sort === "outstanding") {
    primary = compareOptionalNumbers(
      creditOutstandingUsd(left.account, usdInr),
      creditOutstandingUsd(right.account, usdInr),
      "desc"
    );
  } else if (sort === "newapi-spend") {
    primary = compareOptionalNumbers(combinedSpendUsd(left.account), combinedSpendUsd(right.account), "desc");
  } else if (sort === "payable") {
    const leftSpend = combinedSpendUsd(left.account);
    const rightSpend = combinedSpendUsd(right.account);
    primary = compareOptionalNumbers(
      leftSpend == null ? null : amountPayableUsd(leftSpend),
      rightSpend == null ? null : amountPayableUsd(rightSpend),
      "desc"
    );
  } else if (sort === "newapi-o1") {
    primary = compareOptionalNumbers(portalSpendUsd(left.account, "O1"), portalSpendUsd(right.account, "O1"), "desc");
  } else if (sort === "newapi-o2") {
    primary = compareOptionalNumbers(portalSpendUsd(left.account, "O2"), portalSpendUsd(right.account, "O2"), "desc");
  } else if (sort === "actual") {
    primary = compareOptionalNumbers(actualSpendUsd(left.actual, usdInr), actualSpendUsd(right.actual, usdInr), "desc");
  } else if (sort === "tokens") {
    primary = compareOptionalNumbers(sumUsage(left.deployments, usageById, "total_tokens"), sumUsage(right.deployments, usageById, "total_tokens"), "desc");
  } else if (sort === "estimated") {
    primary = compareOptionalNumbers(
      sumUsage(left.deployments, usageById, "estimated_cost_usd"),
      sumUsage(right.deployments, usageById, "estimated_cost_usd"),
      "desc"
    );
  } else if (sort === "deployments") {
    primary = compareOptionalNumbers(left.deployments.length, right.deployments.length, "desc");
  } else if (sort === "gateway") {
    primary = compareOptionalNumbers(gatewayRank(left.account), gatewayRank(right.account), "asc");
  } else if (sort === "owner") {
    primary = compareOptionalText(left.account?.owner_tag, right.account?.owner_tag);
  }
  if (primary !== 0) return primary;
  if (sort === "credits-left" || sort === "credits-left-desc") {
    const direction = sort === "credits-left" ? "asc" : "desc";
    const dollars = compareOptionalNumbers(
      creditRemainingUsd(left.account, usdInr),
      creditRemainingUsd(right.account, usdInr),
      direction
    );
    if (dollars !== 0) return dollars;
  }
  return name;
}

function compareDeployments(
  left: MonitoredModel,
  right: MonitoredModel,
  usageById: Map<number, BreakdownItem>,
  sort: ModelSort
): number {
  const name = compareOptionalText(left.model_name || left.deployment_name, right.model_name || right.deployment_name);
  if (sort === "tokens") {
    const primary = compareOptionalNumbers(
      finiteNumber(usageById.get(left.id)?.total_tokens),
      finiteNumber(usageById.get(right.id)?.total_tokens),
      "desc"
    );
    return primary !== 0 ? primary : name;
  }
  if (sort === "estimated") {
    const primary = compareOptionalNumbers(
      finiteNumber(usageById.get(left.id)?.estimated_cost_usd),
      finiteNumber(usageById.get(right.id)?.estimated_cost_usd),
      "desc"
    );
    return primary !== 0 ? primary : name;
  }
  return name;
}

function sumUsage(
  deployments: MonitoredModel[],
  usageById: Map<number, BreakdownItem>,
  field: "total_tokens" | "estimated_cost_usd"
): number | null {
  let total = 0;
  let seen = false;
  for (const model of deployments) {
    const value = finiteNumber(usageById.get(model.id)?.[field]);
    if (value == null) continue;
    total += value;
    seen = true;
  }
  return seen ? total : null;
}
