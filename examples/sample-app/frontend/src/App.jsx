import { useEffect, useState } from "react";
import { loadItems } from "./api";

export default function App() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    loadItems().then(setItems);
  }, []);
  return <ul>{items.map((i) => <li key={i.id}>{i.name}</li>)}</ul>;
}
