$ErrorActionPreference = 'Stop'
$Compose = 'compose/docker-compose.shard.yml'

function Invoke-MongoEval {
    param(
        [Parameter(Mandatory=$true)][string]$Service,
        [Parameter(Mandatory=$true)][int]$Port,
        [Parameter(Mandatory=$true)][string]$JavaScript,
        [int]$Retries = 30
    )
    $scriptFile = Join-Path $env:TEMP ("mongodb-bigdata-init-" + [guid]::NewGuid().ToString("N") + ".js")
    [System.IO.File]::WriteAllText($scriptFile, $JavaScript, [System.Text.UTF8Encoding]::new($false))
    try {
        for ($attempt = 1; $attempt -le $Retries; $attempt++) {
            $containerOutput = & docker compose --env-file .env -f $Compose ps -q $Service
            $containerItem = $containerOutput | Select-Object -First 1
            $containerId = if ($LASTEXITCODE -eq 0 -and $containerOutput) {
                ([string]$containerItem).Trim()
            } else {
                ''
            }
            if ($LASTEXITCODE -ne 0 -or -not $containerId) {
                if ($attempt -lt $Retries) {
                    Write-Host "Waiting for $Service container to become available ($attempt/$Retries)..."
                    Start-Sleep -Seconds 2
                    continue
                }
                throw "Could not find running container for service '$Service'."
            }
            & docker cp $scriptFile "${containerId}:/tmp/mongosh-init.js"
            if ($LASTEXITCODE -ne 0) { throw "Could not copy the temporary mongosh script into service '$Service'." }
            & docker compose --env-file .env -f $Compose exec -T $Service mongosh --port $Port --quiet --file /tmp/mongosh-init.js
            if ($LASTEXITCODE -eq 0) { return }
            if ($attempt -lt $Retries) {
                Write-Host "Waiting for $Service mongosh to become ready ($attempt/$Retries)..."
                Start-Sleep -Seconds 2
            }
        }
        throw "Could not execute mongosh command in service '$Service' after $Retries attempts. Check Docker logs and HUONG_DAN_XU_LY_LOI.md."
    }
    finally {
        Remove-Item -LiteralPath $scriptFile -Force -ErrorAction SilentlyContinue
    }
}

function Wait-Primary {
    param([Parameter(Mandatory=$true)][string]$Service, [Parameter(Mandatory=$true)][int]$Port)
    $js = 'const end=Date.now()+60000; let h={}; while(Date.now()<end){ h=db.adminCommand({hello:1}); if(h.isWritablePrimary){ print("PRIMARY_READY"); quit(0); } sleep(1000); } printjson(h); quit(2);'
    Invoke-MongoEval -Service $Service -Port $Port -JavaScript $js -Retries 30
}

Write-Host '1/5 Starting config server and the three shard containers...'
& docker compose --env-file .env -f $Compose up -d cfg1 shard1 shard2 shard3
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start the config/shard containers. Check Docker Desktop and available RAM.' }

Write-Host '2/5 Initializing config-server replica set (safe to rerun if already initialized)...'
$cfgInit = 'let s; try { s=db.adminCommand({replSetGetStatus:1}); } catch(e) {} if(s && s.ok===1){ print("cfgrs already initialized"); } else { printjson(rs.initiate({_id:"cfgrs",configsvr:true,members:[{_id:0,host:"cfg1:27019"}]})); }'
Invoke-MongoEval -Service 'cfg1' -Port 27019 -JavaScript $cfgInit
Wait-Primary -Service 'cfg1' -Port 27019

Write-Host '3/5 Initializing each one-member shard replica set (safe to rerun if already initialized)...'
$init1 = 'let s; try { s=db.adminCommand({replSetGetStatus:1}); } catch(e) {} if(s && s.ok===1){ print("sh1 already initialized"); } else { printjson(rs.initiate({_id:"sh1",members:[{_id:0,host:"shard1:27018"}]})); }'
$init2 = 'let s; try { s=db.adminCommand({replSetGetStatus:1}); } catch(e) {} if(s && s.ok===1){ print("sh2 already initialized"); } else { printjson(rs.initiate({_id:"sh2",members:[{_id:0,host:"shard2:27018"}]})); }'
$init3 = 'let s; try { s=db.adminCommand({replSetGetStatus:1}); } catch(e) {} if(s && s.ok===1){ print("sh3 already initialized"); } else { printjson(rs.initiate({_id:"sh3",members:[{_id:0,host:"shard3:27018"}]})); }'
Invoke-MongoEval -Service 'shard1' -Port 27018 -JavaScript $init1
Invoke-MongoEval -Service 'shard2' -Port 27018 -JavaScript $init2
Invoke-MongoEval -Service 'shard3' -Port 27018 -JavaScript $init3
Wait-Primary -Service 'shard1' -Port 27018
Wait-Primary -Service 'shard2' -Port 27018
Wait-Primary -Service 'shard3' -Port 27018

Write-Host '4/5 Starting mongos router...'
& docker compose --env-file .env -f $Compose up -d mongos
if ($LASTEXITCODE -ne 0) { throw 'Could not start mongos. Check its logs and config-server readiness.' }
$ping = 'const end=Date.now()+60000; let r={}; while(Date.now()<end){ try { r=db.adminCommand({ping:1}); if(r.ok===1){ print("MONGOS_READY"); quit(0); } } catch(e){} sleep(1000); } printjson(r); quit(2);'
Invoke-MongoEval -Service 'mongos' -Port 27017 -JavaScript $ping

Write-Host '5/5 Registering only shards not already in the cluster...'
$addMissing = 'const concern=db.adminCommand({setDefaultRWConcern:1,defaultWriteConcern:{w:"majority"}}); if(concern.ok!==1) print("WARN setDefaultRWConcern: "+tojson(concern)); const r=db.adminCommand({listShards:1}); if(r.ok!==1) throw new Error(tojson(r)); const ids=r.shards.map(s=>s._id); const wanted=[["sh1","sh1/shard1:27018"],["sh2","sh2/shard2:27018"],["sh3","sh3/shard3:27018"]]; for(const x of wanted){ if(ids.includes(x[0])) print(x[0]+" already registered"); else printjson(sh.addShard(x[1])); } printjson(sh.status());'
Invoke-MongoEval -Service 'mongos' -Port 27017 -JavaScript $addMissing

Write-Host ''
Write-Host 'Sharded cluster initialization/verification commands completed.'
Write-Host 'Verify: docker compose -f compose/docker-compose.shard.yml exec -T mongos mongosh --quiet --eval "sh.status()"'
Write-Host 'Do not run standalone and mongos on host port 27017 at the same time.'
