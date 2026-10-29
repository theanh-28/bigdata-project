// Run through mongos after init_sharded.ps1 completes.
// Collections must be empty before shardCollection for the planned initial chunk setup.
sh.enableSharding("yelp");
sh.shardCollection("yelp.reviews_range", { seq: 1 });
sh.shardCollection("yelp.reviews_hashed_seq", { seq: "hashed" });
sh.shardCollection("yelp.reviews_targeted", { review_id: "hashed" });
db.getSiblingDB("yelp").reviews_targeted.createIndex({ business_id: 1 });
// Inspect initial chunk placement before the measured insert phase. If you need a controlled snapshot,
// wait for initial placement, then stop the balancer manually with: sh.stopBalancer()
printjson(sh.status());
