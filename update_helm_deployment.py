
with open(r"charts\llm-shield-proxy\templates\deployment.yaml", "r", encoding="utf-8") as f:
    text = f.read()

# Insert podSecurityContext
pod_sec_target = "    spec:\n"
pod_sec_replacement = """    spec:
      {{- with .Values.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}\n"""
text = text.replace(pod_sec_target, pod_sec_replacement)

# Insert securityContext under the container
container_target = "          imagePullPolicy: {{ .Values.image.pullPolicy }}\n"
container_replacement = """          imagePullPolicy: {{ .Values.image.pullPolicy }}
          {{- with .Values.securityContext }}
          securityContext:
            {{- toYaml . | nindent 12 }}
          {{- end }}\n"""
text = text.replace(container_target, container_replacement)

with open(r"charts\llm-shield-proxy\templates\deployment.yaml", "w", encoding="utf-8") as f:
    f.write(text)
