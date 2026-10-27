import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

// Loads one thing from the API and reports loading / error / data, so a failure in one panel never blanks the page.
export default function useLoad(path) {
  const [state, setState] = useState({ loading: true, data: null, error: null, status: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    api(path)
      .then((data) => live && setState({ loading: false, data, error: null, status: 200 }))
      .catch((e) => live && setState({ loading: false, data: null, error: e.message, status: e.status }));
    return () => {
      live = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { ...state, reload };
}
