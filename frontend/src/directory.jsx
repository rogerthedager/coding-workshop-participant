import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { useAuth } from './auth';

// The API returns IDs only. This loads the reference data needed to show names:
// categories and locations for everyone, engineers for staff, users for admins.
const DirectoryContext = createContext(null);

export function DirectoryProvider({ children }) {
  const { user, isStaff, isAdmin } = useAuth();
  const [data, setData] = useState({ categories: [], buildings: [], engineers: [], users: [] });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const signedIn = Boolean(user && !user.must_change_password);

  useEffect(() => {
    if (!signedIn) return undefined;
    let live = true;
    const none = { items: [] };
    Promise.all([
      api.categories().catch(() => none),
      api.locations().catch(() => none),
      isStaff ? api.engineers().catch(() => none) : none,
      isAdmin ? api.users().catch(() => none) : none,
    ]).then(([categories, locations, engineers, users]) => {
      if (!live) return;
      setData({
        categories: categories.items,
        buildings: locations.items,
        engineers: engineers.items,
        users: users.items,
      });
    });
    return () => {
      live = false;
    };
  }, [signedIn, isStaff, isAdmin, tick, user?.id]);

  const value = useMemo(() => {
    const byId = (items) => new Map(items.map((item) => [item.id, item]));
    const categories = byId(data.categories);
    const buildings = byId(data.buildings);
    const floors = new Map();
    const seats = new Map();
    for (const building of data.buildings) {
      for (const floor of building.floors || []) {
        floors.set(floor.id, floor);
        for (const seat of floor.seats || []) seats.set(seat.id, seat);
      }
    }
    const people = new Map();
    for (const item of data.users) people.set(item.id, item.full_name);
    for (const item of data.engineers) people.set(item.user_id, item.user.full_name);
    if (user) people.set(user.id, user.full_name);

    return {
      ...data,
      reload,
      category(id) {
        const item = categories.get(id);
        if (!item) return '';
        const parent = item.parent_id && categories.get(item.parent_id);
        return parent ? `${parent.name} › ${item.name}` : item.name;
      },
      location(incident, separator = ' · ') {
        const parts = [
          buildings.get(incident.building_id)?.code || buildings.get(incident.building_id)?.name,
          floors.get(incident.floor_id)?.name,
          seats.get(incident.seat_id)?.code,
        ].filter(Boolean);
        return parts.join(separator) || '—';
      },
      /** Display name for a user ID; `fallback` covers people the caller cannot look up. */
      person(id, fallback = 'Facilities team') {
        if (!id) return 'Unassigned';
        if (user && id === user.id) return 'You';
        return people.get(id) || fallback;
      },
      engineerByUser: (id) => data.engineers.find((item) => item.user_id === id),
    };
  }, [data, reload, user]);

  return <DirectoryContext.Provider value={value}>{children}</DirectoryContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDirectory() {
  return useContext(DirectoryContext);
}
