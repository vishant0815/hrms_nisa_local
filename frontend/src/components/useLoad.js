import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

// Loads one thing from the API and reports loading / error / data, so a failure in one panel never blanks the page.
// A null path means "not yet": nothing is fetched. Changing the path clears the old data; reloading the same path keeps
// it on screen while the new copy arrives.
export default function useLoad(path) {
  const empty = { loading: !!path, data: null, error: null, status: null, path };
  const [state, setState] = useState(empty);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    if (!path) {
      setState({ loading: false, data: null, error: null, status: null, path });
      return undefined;
    }
    setState((s) => (s.path === path ? { ...s, loading: true } : { loading: true, data: null, error: null, status: null, path }));
    api(path)
      .then((data) => live && setState({ loading: false, data, error: null, status: 200, path }))
      .catch((e) => live && setState({ loading: false, data: null, error: e.message, status: e.status, path }));
    return () => {
      live = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { ...state, reload };
}
