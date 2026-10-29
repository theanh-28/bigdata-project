// Run this script through mongos AFTER scripts/init_sharded.ps1 has completed.
// Keep the E5c collection separate from E5a/E5b and from the standalone baseline.
const ns = "yelp.reviews_e5c";
const cfg = db.getSiblingDB("config");
const existing = cfg.collections.findOne({ _id: ns }, { _id: 1, key: 1 });

if (existing) {
  if ((existing.key || {}).review_id !== "hashed") {
    throw new Error(`${ns} is already sharded with a different key: ${tojson(existing.key)}`);
  }
  print(`${ns} is already sharded on { review_id: "hashed" }; reusing it.`);
} else {
  sh.enableSharding("yelp");
  print(`Sharding ${ns} on { review_id: "hashed" }...`);
  printjson(sh.shardCollection(ns, { review_id: "hashed" }));
}

print("Verify shard membership/chunk placement. Keep the balancer enabled for E5c.");
printjson(sh.status());
