# OpenZeppelin Monitor BlockWatcher Architecture

## Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Initialization Flow](#initialization-flow)
4. [Component Dependencies](#component-dependencies)
5. [Block Processing Pipeline](#block-processing-pipeline)
6. [Network Watcher Lifecycle](#network-watcher-lifecycle)
7. [Filter and Trigger Flow](#filter-and-trigger-flow)
8. [Storage and Tracking](#storage-and-tracking)
9. [End-to-End Data Flow](#end-to-end-data-flow)
10. [RPC Client Architecture](#rpc-client-architecture)

## Overview

The OpenZeppelin Monitor BlockWatcher is a sophisticated blockchain monitoring system that watches multiple networks concurrently, processes blocks according to configured monitors, and triggers notifications based on matched conditions. The system is built with Rust and uses an async, event-driven architecture.

## High-Level Architecture

```mermaid
graph TB
    subgraph "Entry Points"
        MAIN[main.rs]
        CLI[CLI Arguments]
    end

    subgraph "Configuration Layer"
        NC[Network Configs]
        MC[Monitor Configs]
        TC[Trigger Configs]
    end

    subgraph "Service Layer"
        NS[NetworkService]
        MS[MonitorService]
        TS[TriggerService]
        FS[FilterService]
        TES[TriggerExecutionService]
        NOTS[NotificationService]
    end

    subgraph "Client Layer"
        CP[ClientPool]
        EVMC[EVM Client]
        SC[Stellar Client]
    end

    subgraph "Block Processing"
        BWS[BlockWatcherService]
        NBW[NetworkBlockWatcher]
        BT[BlockTracker]
        BS[BlockStorage]
    end

    subgraph "Handlers"
        BH[Block Handler]
        TH[Trigger Handler]
    end

    subgraph "External Systems"
        BC1[EVM Networks]
        BC2[Stellar Networks]
        NOTIF[Notification Channels]
    end

    %% Initialization Flow
    MAIN --> CLI
    CLI --> NS
    CLI --> MS
    CLI --> TS

    %% Config Loading
    NC --> NS
    MC --> MS
    TC --> TS

    %% Service Dependencies
    MS --> NS
    MS --> TS
    TES --> NOTS
    FS --> TES

    %% Client Initialization
    NS --> CP
    CP --> EVMC
    CP --> SC

    %% Block Processing Setup
    BWS --> NBW
    NBW --> BT
    NBW --> BS
    BWS --> BH
    BWS --> TH

    %% Handler Dependencies
    BH --> FS
    TH --> TES

    %% External Connections
    EVMC --> BC1
    SC --> BC2
    NOTS --> NOTIF

    style MAIN fill:#e1f5fe
    style BWS fill:#c8e6c9
    style CP fill:#fff3e0
    style NOTIF fill:#fce4ec
```

## Initialization Flow

```mermaid
sequenceDiagram
    participant Main as main.rs
    participant Boot as bootstrap::initialize_services
    participant NS as NetworkService
    participant MS as MonitorService
    participant TS as TriggerService
    participant CP as ClientPool
    participant BWS as BlockWatcherService

    Main->>Boot: initialize_services()
    
    Boot->>NS: new(NetworkRepository)
    NS-->>Boot: Load networks from config/networks/*.json
    
    Boot->>TS: new(TriggerRepository)
    TS-->>Boot: Load triggers from config/triggers/*.json
    
    Boot->>MS: new(MonitorRepository)
    MS-->>Boot: Load monitors from config/monitors/*.json
    
    Note over Boot: Filter active monitors
    Boot->>Boot: filter_active_monitors()
    
    Boot->>CP: new()
    Note over CP: Initialize RPC clients lazily
    
    Main->>Main: get_contract_specs(client_pool, monitors)
    
    Main->>Main: create_block_handler()
    Main->>Main: create_trigger_handler()
    
    Main->>BWS: new(storage, handlers, tracker)
    
    loop For each network with monitors
        Main->>BWS: start_network_watcher(network, client)
        BWS->>BWS: NetworkBlockWatcher::new()
        BWS->>BWS: Schedule cron job
    end
```

## Component Dependencies

```mermaid
graph LR
    subgraph "Core Dependencies"
        M[Monitor] -->|requires| N[Network]
        M -->|requires| T[Trigger]
        M -->|optional| CS[ContractSpec]
    end

    subgraph "Service Dependencies"
        BWS[BlockWatcherService] -->|requires| M
        BWS -->|requires| BH[BlockHandler]
        BWS -->|requires| TH[TriggerHandler]
        BWS -->|requires| BS[BlockStorage]
        BWS -->|requires| BT[BlockTracker]
        
        BH -->|requires| FS[FilterService]
        BH -->|requires| CP[ClientPool]
        BH -->|requires| CS
        
        TH -->|requires| TES[TriggerExecutionService]
        TH -->|requires| LS[LoadedScripts]
        
        TES -->|requires| NS[NotificationService]
        TES -->|requires| SE[ScriptExecutor]
    end

    subgraph "Storage Dependencies"
        BT -->|optional| BS
        BS -->|writes| LB[last_block.txt]
        BS -->|writes| BF[blocks_*.json]
        BS -->|writes| MB[missed_blocks.txt]
    end

    style BWS fill:#c8e6c9
    style M fill:#fff3e0
    style BH fill:#e1f5fe
    style TH fill:#fce4ec
```

## Block Processing Pipeline

```mermaid
flowchart TD
    START[Cron Trigger] --> PNB[process_new_blocks]
    
    PNB --> GETLAST[Get Last Processed Block]
    GETLAST --> GETLATEST[Get Latest Block from RPC]
    
    GETLATEST --> CALC[Calculate Block Range]
    CALC --> |"start_block to latest_confirmed"| FETCH[Fetch Blocks]
    
    FETCH --> PIPELINE[Create Processing Pipeline]
    
    PIPELINE --> CH1[Channel 1: process_tx/rx]
    PIPELINE --> CH2[Channel 2: trigger_tx/rx]
    
    subgraph "Stage 1: Block Processing"
        CH1 --> RECORD[Record Block in Tracker]
        RECORD --> SEND1[Send to process_tx]
        SEND1 --> CONCURRENT[Concurrent Processing]
        CONCURRENT --> |"buffer_unordered(32)"| HANDLER[block_handler()]
        HANDLER --> PROCESSED[ProcessedBlock]
    end
    
    subgraph "Stage 2: Trigger Pipeline"
        PROCESSED --> SEND2[Send to trigger_tx]
        SEND2 --> BTREE[BTreeMap for Ordering]
        BTREE --> ORDERED[Process in Order]
        ORDERED --> TRIGGER[trigger_handler()]
        TRIGGER --> NOTIFY[Spawn Notification Task]
    end
    
    subgraph "Stage 3: Persistence"
        NOTIFY --> STORE{store_blocks?}
        STORE -->|Yes| DELETE[Delete Old Blocks]
        DELETE --> SAVE[Save New Blocks]
        STORE -->|No| UPDATE
        SAVE --> UPDATE[Update Last Processed]
    end
    
    UPDATE --> END[Complete]
    
    style START fill:#e8f5e9
    style PIPELINE fill:#e1f5fe
    style TRIGGER fill:#fce4ec
    style END fill:#c8e6c9
```

## Network Watcher Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Created: NetworkBlockWatcher::new()
    
    Created --> Initialized: watcher.start(rpc_client)
    
    Initialized --> Scheduled: JobScheduler.add(Job)
    
    Scheduled --> Running: scheduler.start()
    
    Running --> Processing: Cron trigger
    Processing --> Running: Complete
    
    Running --> Stopping: stop_network_watcher()
    
    Stopping --> Shutdown: scheduler.shutdown()
    
    Shutdown --> [*]
    
    Processing --> Error: RPC failure
    Error --> Processing: Retry with backoff
    
    note right of Processing
        process_new_blocks():
        1. Get last processed block
        2. Fetch new blocks
        3. Process through pipeline
        4. Update storage
    end note
    
    note right of Running
        Executes on cron schedule:
        - Every minute (*/1 * * * *)
        - Or custom schedule
    end note
```

## Filter and Trigger Flow

```mermaid
flowchart LR
    subgraph "Filter Stage"
        BLOCK[Block Data] --> FILTER[FilterService]
        MONITOR[Monitor Conditions] --> FILTER
        SPECS[Contract Specs] --> FILTER
        
        FILTER --> MATCH{Matches?}
        MATCH -->|Yes| MM[MonitorMatch]
        MATCH -->|No| SKIP[Skip]
    end
    
    subgraph "Trigger Filter Stage"
        MM --> TF[run_trigger_filters]
        SCRIPTS[Loaded Scripts] --> TF
        
        TF --> EXEC[execute_trigger_condition]
        EXEC --> RESULT{Filter Out?}
        RESULT -->|Yes| FILTERED[Exclude]
        RESULT -->|No| KEEP[Keep Match]
    end
    
    subgraph "Notification Stage"
        KEEP --> HM[handle_match]
        HM --> TES[TriggerExecutionService]
        TES --> PREP[Prepare Payload]
        PREP --> SEND[Send Notification]
        
        SEND --> SLACK[Slack]
        SEND --> EMAIL[Email]
        SEND --> DISCORD[Discord]
        SEND --> WEBHOOK[Webhook]
        SEND --> SCRIPT[Custom Script]
    end
    
    style BLOCK fill:#e8f5e9
    style MM fill:#fff3e0
    style KEEP fill:#c8e6c9
    style SEND fill:#fce4ec
```

## Storage and Tracking

```mermaid
graph TD
    subgraph "FileBlockStorage"
        FS[FileBlockStorage] --> |writes| LBF["{network}_last_block.txt"]
        FS --> |writes| BLF["{network}_blocks_{timestamp}.json"]
        FS --> |writes| MBF["{network}_missed_blocks.txt"]
    end
    
    subgraph "BlockTracker"
        BT[BlockTracker] --> HM[HashMap: network -> VecDeque]
        HM --> |maintains| HISTORY["Last 1000 blocks per network"]
        
        BT --> GAP{Gap Detection}
        GAP --> |"current > last + 1"| MISSED[Log Missed Blocks]
        MISSED --> MBF
        
        BT --> DUP{Duplicate Detection}
        DUP --> |"current <= last"| WARN[Log Warning]
    end
    
    subgraph "Data Directory Structure"
        DIR[data/]
        DIR --> LBF
        DIR --> BLF
        DIR --> MBF
    end
    
    style FS fill:#e8f5e9
    style BT fill:#e1f5fe
    style DIR fill:#fff3e0
```

## End-to-End Data Flow

```mermaid
sequenceDiagram
    participant Blockchain
    participant RPC as RPC Client
    participant BWS as BlockWatcherService
    participant BH as BlockHandler
    participant FS as FilterService
    participant TH as TriggerHandler
    participant TES as TriggerExecutionService
    participant NS as NotificationService
    participant Storage
    
    Note over BWS: Cron triggers every minute
    
    BWS->>Storage: get_last_processed_block()
    Storage-->>BWS: last_block_number
    
    BWS->>RPC: get_latest_block_number()
    RPC->>Blockchain: eth_blockNumber
    Blockchain-->>RPC: latest_block
    RPC-->>BWS: latest_block
    
    BWS->>BWS: Calculate range (last+1 to latest-confirmations)
    
    BWS->>RPC: get_blocks(start, end)
    RPC->>Blockchain: eth_getBlockByNumber (multiple)
    Blockchain-->>RPC: blocks
    RPC-->>BWS: Vec<BlockType>
    
    loop For each block
        BWS->>BH: process block
        BH->>FS: filter_block(block, monitors)
        FS-->>BH: Vec<MonitorMatch>
        BH-->>BWS: ProcessedBlock
        
        BWS->>TH: handle ProcessedBlock
        TH->>TH: run_trigger_filters()
        
        alt Has matches after filtering
            TH->>TES: execute_triggers(matches)
            TES->>NS: send_notification()
            NS->>NS: Format message
            NS-->>NS: Send to channel
        end
    end
    
    BWS->>Storage: save_last_processed_block(latest_confirmed)
    
    opt If store_blocks enabled
        BWS->>Storage: save_blocks(blocks)
    end
```

## RPC Client Architecture

```mermaid
graph TB
    subgraph "Client Pool"
        CP[ClientPool]
        CP --> EVMMAP["HashMap<network_slug, EvmClient>"]
        CP --> STELLARMAP["HashMap<network_slug, StellarClient>"]
    end
    
    subgraph "Transport Layer"
        HTC[HttpTransportClient]
        HTC --> EM[EndpointManager]
        EM --> RETRY[RetryableClient]
        RETRY --> |"exponential backoff"| RPC1[Primary RPC]
        RETRY --> |"failover"| RPC2[Fallback RPC]
    end
    
    subgraph "Network Clients"
        EVMC[EvmClient]
        SC[StellarClient]
        EVMC --> EVMT[EVMTransportClient]
        SC --> STELT[StellarTransportClient]
        EVMT --> HTC
        STELT --> HTC
    end
    
    subgraph "Retry Strategy"
        STRATEGY[TransientErrorRetryStrategy]
        STRATEGY --> |"5xx errors"| RETRY_YES[Retry]
        STRATEGY --> |"429 rate limit"| ROTATE[Rotate Endpoint]
        STRATEGY --> |"4xx errors"| NO_RETRY[Don't Retry]
    end
    
    EM --> STRATEGY
    
    style CP fill:#fff3e0
    style HTC fill:#e1f5fe
    style STRATEGY fill:#fce4ec
```

## Key Configuration Parameters

### Network Configuration

- **cron_schedule**: When to check for new blocks (e.g., `"*/1 * * * *"` for every minute)
- **confirmation_blocks**: How many blocks to wait before considering a block confirmed
- **max_past_blocks**: Maximum number of historical blocks to process (auto-calculated if not set)
- **store_blocks**: Whether to persist block data to disk
- **block_time_ms**: Expected time between blocks (used for calculations)

### Monitor Configuration

- **networks**: Which networks this monitor applies to
- **addresses**: Contract addresses to monitor
- **match_conditions**: Events, functions, and transaction conditions to match
- **trigger_conditions**: Scripts to filter matches before triggering
- **triggers**: Notification configurations to execute

### Processing Constants

- **Buffer size**: 32 concurrent blocks processed at once
- **Channel buffer**: 2x block count for pipeline channels
- **History size**: 1000 blocks kept in memory per network
- **Retry base**: 1 second exponential backoff for RPC retries

## Critical Dependencies

1. **BlockWatcher CANNOT run without**:
   - At least one active (non-paused) monitor
   - Network configuration for monitored networks
   - RPC endpoints accessible and responding

2. **Processing REQUIRES**:
   - Sequential block processing (no gaps)
   - Ordered trigger execution
   - Persistent storage for recovery

3. **Performance Bottlenecks**:
   - RPC response time
   - Contract spec fetching (cached after first fetch)
   - Script execution for trigger filters
   - Notification service delivery

## Python Integration Strategy

To run only the blockwatcher components from Python:

1. **Create minimal monitor configuration**:
   - Monitor all blocks (empty match conditions)
   - Apply to desired networks
   - No trigger conditions

2. **Create empty trigger configuration**:
   - No notification endpoints
   - Prevents any alerts

3. **Run the Rust binary**:
   - Use subprocess to execute `./openzeppelin-monitor`
   - Monitor stdout/stderr for logs
   - Watch `data/` directory for processing status

4. **Provide control interface**:
   - Start/stop monitoring
   - Check processing status
   - View last processed blocks
   - Detect missed blocks

This approach leverages the production-tested Rust implementation while providing a focused blockwatcher interface.
