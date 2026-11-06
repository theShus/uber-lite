{{/* ime servisa: ime release-a, osim ako je postavljen nameOverride */}}
{{- define "microservice.name" -}}
{{- default .Release.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* pod labels, ono sto identifikuje podove , i koje cemo koristiti u matchLables tako da moraju da budu uinque a ne hardcoded */}}
{{- define "microservice.selectorLabels" -}}
app.kubernetes.io/name: {{ include "microservice.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Verzija u labelama: image tag, ili appVersion iz Chart.yaml. */}}
{{- define "microservice.version" -}}
{{- .Values.image.tag | default .Chart.AppVersion | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Sve labele: selector labele + verzija i vlasništvo. */}}
{{- define "microservice.labels" -}}
{{ include "microservice.selectorLabels" . }}
app.kubernetes.io/version: {{ include "microservice.version" . | quote }}
app.kubernetes.io/part-of: uber-lite
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}


{{/* repository@digest, ili repository:tag. Render pada ako repository nije postavljen. */}}
{{- define "microservice.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{ $repo }}@{{ .Values.image.digest }}
{{- else -}}
{{ $repo }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}
{{- end -}}

{{/* Jedan NetworkPolicy peer iz unosa u values-u: ipBlock, ili namespace i/ili pod selektor (jedan element = AND). */}}
{{- define "microservice.peer" -}}
{{- if .cidr }}
ipBlock:
  cidr: {{ .cidr }}
{{- else }}
{{- with .namespace }}
namespaceSelector:
  matchLabels:
    kubernetes.io/metadata.name: {{ . }}
{{- end }}
{{- if or .podName .podLabels }}
podSelector:
  matchLabels:
    {{- with .podName }}
    app.kubernetes.io/name: {{ . }}
    {{- end }}
    {{- with .podLabels }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Ime migracije: ime Job-a, NetworkPolicy-ja i labela pod-a migracije. */}}
{{- define "microservice.migrationName" -}}
{{- printf "%s-migrate" (include "microservice.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
