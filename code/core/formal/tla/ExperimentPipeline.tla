---- MODULE ExperimentPipeline ----
EXTENDS Naturals, FiniteSets
CONSTANT Mode
Stages == {"INIT","ENV_AUDIT","CONFIG_VALIDATION","NUMA_EXPERIMENT","NUMA_GATE",
           "FACTORIAL_EXPERIMENT","FINAL_VALIDATION","DATA_ANALYSIS","REPORT_GENERATION","DONE","FAILED"}
Measured == {"NUMA_EXPERIMENT","FACTORIAL_EXPERIMENT","FINAL_VALIDATION"}
Variants == {"A","B","C","D"}
Keys == Measured \X Variants
Cells(s) == IF s = "FACTORIAL_EXPERIMENT" THEN Variants ELSE {"A","B"}
Original(s,c) == s = "NUMA_EXPERIMENT" \/ c = "A" \/ (s = "FACTORIAL_EXPERIMENT" /\ c = "B")
Policy(s,c,b) ==
  IF s = "FINAL_VALIDATION" /\ c = "B" THEN b
  ELSE IF s # "FINAL_VALIDATION" /\ c \in {"B","D"} THEN "local" ELSE "default"
VARIABLES phase, gate, best, active, passed, failed, counted, runs
vars == <<phase,gate,best,active,passed,failed,counted,runs>>
Record(s,c,b) == [workload |-> "C7552", solver |-> "KLU", threads |-> 8, ns |-> 20,
                   netlist |-> "fixed-netlist", pdk |-> "fixed-pdk", cpu |-> "0-15",
                   binary |-> IF Original(s,c) THEN "original" ELSE "verified-klu8-bolt",
                   memory |-> Policy(s,c,b)]
Init == /\ phase = "INIT" /\ gate = FALSE /\ best = "default" /\ active = {}
        /\ passed = {} /\ failed = {} /\ counted = {}
        /\ runs = [k \in Keys |-> Record(k[1],k[2],"default")]
NextPhase(p,q) ==
  /\ phase = p /\ phase' = q /\ UNCHANGED <<gate,best,active,passed,failed,counted,runs>>
Start ==
  \E c \in Cells(phase):
    LET k == <<phase,c>> IN
      /\ phase \in Measured /\ active = {} /\ k \notin (passed \cup failed)
      /\ active' = {k} /\ runs' = [runs EXCEPT ![k] = Record(phase,c,best)]
      /\ UNCHANGED <<phase,gate,best,passed,failed,counted>>
Success ==
  \E k \in active:
    /\ active' = {} /\ passed' = passed \cup {k}
    /\ UNCHANGED <<phase,gate,best,failed,counted,runs>>
Fail ==
  /\ active # {} /\ failed' = failed \cup active /\ active' = {} /\ phase' = "FAILED"
  /\ UNCHANGED <<gate,best,passed,counted,runs>>
Finish(s,t) ==
  /\ phase = s /\ active = {} /\ (\A c \in Cells(s): <<s,c>> \in passed)
  /\ phase' = t /\ counted' = counted \cup {s}
  /\ UNCHANGED <<gate,best,active,passed,failed,runs>>
Gate ==
  /\ phase = "NUMA_GATE" /\ gate' \in BOOLEAN
  /\ phase' = IF gate' THEN "FACTORIAL_EXPERIMENT" ELSE "FINAL_VALIDATION"
  /\ UNCHANGED <<best,active,passed,failed,counted,runs>>
CompleteFactorial ==
  /\ phase = "FACTORIAL_EXPERIMENT" /\ active = {}
  /\ (\A c \in Variants: <<phase,c>> \in passed)
  /\ best' \in {"default","local"} /\ counted' = counted \cup {phase}
  /\ phase' = "FINAL_VALIDATION" /\ UNCHANGED <<gate,active,passed,failed,runs>>
Next == \/ NextPhase("INIT","ENV_AUDIT")
        \/ NextPhase("ENV_AUDIT","CONFIG_VALIDATION")
        \/ NextPhase("CONFIG_VALIDATION","NUMA_EXPERIMENT")
        \/ Start \/ Success \/ Fail
        \/ Finish("NUMA_EXPERIMENT","NUMA_GATE") \/ Gate \/ CompleteFactorial
        \/ Finish("FINAL_VALIDATION","DATA_ANALYSIS")
        \/ NextPhase("DATA_ANALYSIS","REPORT_GENERATION")
        \/ NextPhase("REPORT_GENERATION","DONE")
        \/ /\ phase \in {"DONE","FAILED"} /\ UNCHANGED vars
Spec == Init /\ [][Next]_vars
TypeOK == phase \in Stages /\ gate \in BOOLEAN /\ best \in {"default","local"}
          /\ active \subseteq Keys /\ passed \subseteq Keys /\ failed \subseteq Keys
          /\ counted \subseteq Measured
Fixed == \A k \in (active \cup passed): /\ runs[k].workload = "C7552"
              /\ runs[k].solver = "KLU" /\ runs[k].threads = 8 /\ runs[k].ns = 20
              /\ runs[k].cpu = "0-15" /\ runs[k].pdk = "fixed-pdk"
              /\ runs[k].netlist = "fixed-netlist" /\ runs[k].binary = Record(k[1],k[2],best).binary
              /\ runs[k].memory = Policy(k[1],k[2],best)
GateOK == (phase = "FACTORIAL_EXPERIMENT" \/ "FACTORIAL_EXPERIMENT" \in counted) => gate
FinalOK == << "FINAL_VALIDATION", "B" >> \in passed =>
          runs[<<"FINAL_VALIDATION","B">>] = Record("FINAL_VALIDATION","B",best)
Lock == Cardinality(active) <= 1
Preserve == active \cap (passed \cup failed) = {}
ValidStats == passed \cap failed = {} /\ (\A s \in counted: \A c \in Cells(s): <<s,c>> \in passed)
Canary == Mode # "negative-control" \/ phase # "NUMA_EXPERIMENT"
====
