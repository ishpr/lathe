// A React stub: two fetch calls, one to a route the backend does not have.
export async function loadItems() {
  const res = await fetch("/items");
  return res.json();
}

export async function loadReport() {
  const res = await fetch("/reports/summary");
  return res.json();
}
