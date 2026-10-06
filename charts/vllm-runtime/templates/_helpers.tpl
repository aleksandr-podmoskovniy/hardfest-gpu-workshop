{{- define "vllm.name" -}}
{{- default .Release.Name .Values.fullnameOverride -}}
{{- end -}}

{{- define "vllm.labels" -}}
app.kubernetes.io/part-of: hardfest-gpu-workshop
app.kubernetes.io/name: {{ include "vllm.name" . | quote }}
{{- end -}}

{{- define "vllm.config" -}}
{{- toYaml .Values.vllm -}}
{{- end -}}

{{- define "vllm.devices" -}}
requests:
  - name: gpu
    exactly:
      deviceClassName: {{ .Values.dra.deviceClassName | quote }}
      allocationMode: ExactCount
      count: {{ .Values.dra.count }}
      {{- with .Values.dra.selectors }}
      selectors:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.dra.capacity }}
      capacity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- with .Values.dra.config }}
config:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "vllm.claimName" -}}
{{- printf "%s-%s" (include "vllm.name" .) (include "vllm.devices" . | sha256sum | trunc 10) -}}
{{- end -}}

{{- define "vllm.validate" -}}
{{- if not (regexMatch "^[a-z][a-z0-9-]{0,40}[a-z0-9]$" (include "vllm.name" .)) -}}
{{- fail "release/fullnameOverride must be a DNS name of 2..42 characters" -}}
{{- end -}}
{{- if ne (int .Values.dra.count) (int (default 1 (index .Values.vllm "tensor-parallel-size"))) -}}
{{- fail "dra.count must equal vllm.tensor-parallel-size" -}}
{{- end -}}
{{- if hasKey .Values.vllm "cpu-offload-gb" -}}
{{- fail "weight offload is not part of the workshop profiles" -}}
{{- end -}}
{{- if or (ne (int .Values.vllm.port) 8000) (ne .Values.vllm.host "0.0.0.0") -}}
{{- fail "vllm must listen on 0.0.0.0:8000 for Service and probes" -}}
{{- end -}}
{{- $names := dict -}}
{{- if and (not (empty .Values.modelRefs)) (not (empty .Values.modelVolumes)) -}}
{{- fail "choose modelRefs (ai-models) or modelVolumes (PVC), not both" -}}
{{- end -}}
{{- range $flag := list "enable-prefix-caching" "enable-chunked-prefill" -}}
{{- if and (hasKey $.Values.vllm $flag) (hasKey $.Values.vllm (printf "no-%s" $flag)) -}}
{{- fail (printf "conflicting positive and negative flag: %s" $flag) -}}
{{- end -}}
{{- end -}}
{{- range .Values.modelVolumes -}}
{{- if or (hasPrefix "/" .subPath) (has ".." (splitList "/" .subPath)) -}}
{{- fail "modelVolumes.subPath must stay inside the model PVC" -}}
{{- end -}}
{{- if or (has .name (list "profile" "runtime" "shm")) (hasKey $names .name) -}}
{{- fail "modelVolumes names must be unique and not profile/runtime/shm" -}}
{{- end -}}
{{- $_ := set $names .name true -}}
{{- end -}}
{{- if gt (int .Values.replicaCount) 0 -}}
{{- if or (and (empty .Values.modelVolumes) (empty .Values.modelRefs)) (empty .Values.nodeSelector) -}}
{{- fail "running workloads require modelRefs or modelVolumes and a nodeSelector" -}}
{{- end -}}
{{- $paths := list .Values.vllm.model -}}
{{- with index .Values.vllm "speculative-config" -}}
{{- with .model -}}
{{- $paths = append $paths . -}}
{{- end -}}
{{- end -}}
{{- range $paths -}}
{{- $path := . -}}
{{- if hasPrefix "/" $path -}}
{{- $mounted := false -}}
{{- range $.Values.modelRefs -}}
{{- $root := printf "/data/modelcache/models/%s" . -}}
{{- if or (eq $path $root) (hasPrefix (printf "%s/" $root) $path) -}}
{{- $mounted = true -}}
{{- end -}}
{{- end -}}
{{- range $.Values.modelVolumes -}}
{{- if or (eq $path .mountPath) (hasPrefix (printf "%s/" .mountPath) $path) -}}
{{- $mounted = true -}}
{{- end -}}
{{- end -}}
{{- if not $mounted -}}
{{- fail (printf "model path %s has no modelVolumes mount or matching modelRefs entry" $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $key := list "image" "dra" "modelVolumes" "modelRefs" "nodeSelector" "vllm" -}}
{{- if contains "REPLACE_" (toJson (index $.Values $key)) -}}
{{- fail (printf "replace site placeholders in %s before enabling replicas" $key) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
