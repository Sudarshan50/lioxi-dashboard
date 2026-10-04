import { ChannelCard } from "@/types";

export type ChannelRow<T extends { id: number }> = {
  key: string;
  item: T;
  channel?: ChannelCard;
  lead: boolean;
};

/** One visual row per NewAPI channel. Account-level fields stay on the lead row. */
export function expandChannelCards<T extends { id: number; channel_cards?: ChannelCard[] }>(
  items: T[]
): ChannelRow<T>[] {
  return items.flatMap((item): ChannelRow<T>[] => {
    const cards = item.channel_cards ?? [];
    if (cards.length <= 1) return [{ key: String(item.id), item, lead: true }];
    return cards.map((channel, index) => ({
      key: `${item.id}-${channel.gateway}-${channel.id ?? index}`,
      item,
      channel,
      lead: index === 0,
    }));
  });
}
