import { useNavigate } from "react-router-dom";
import { ShoppingCart, Truck, Zap, ArrowRight, BarChart3, Shield, GitBranch } from "lucide-react";

export default function RoleSelection() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3">
        <div className="max-w-5xl mx-auto flex items-center gap-2.5">
          <div className="h-7 w-7 rounded-md bg-[#0070d2] flex items-center justify-center">
            <Zap className="h-4 w-4 text-white" />
          </div>
          <span className="text-sm font-semibold text-slate-800 tracking-tight">
            TradeBridge AI
          </span>
          <span className="ml-2 text-xs text-slate-400 border border-gray-200 rounded px-1.5 py-0.5 bg-gray-50">
            B2B Negotiation Platform
          </span>
        </div>
      </header>

      {/* Hero */}
      <div className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        <div className="text-center mb-10 max-w-lg">
          <div className="inline-flex items-center gap-2 bg-blue-50 border border-blue-100 rounded-full px-3 py-1 mb-4">
            <span className="h-1.5 w-1.5 rounded-full bg-[#0070d2]" />
            <span className="text-xs font-medium text-[#0070d2]">
              KI-gestützte Verhandlungsautomatisierung
            </span>
          </div>
          <h1 className="text-2xl font-bold text-slate-900 mb-3 leading-tight">
            Intelligente B2B-Verhandlungen —<br />
            <span className="text-[#0070d2]">automatisiert und transparent</span>
          </h1>
          <p className="text-sm text-slate-500 leading-relaxed">
            KI-Agenten verhandeln eigenständig nach Ihren Interessen. 
            ZOPA-Analyse, multivariate Verhandlung und Human-in-the-Loop — 
            alles in einer Plattform.
          </p>
        </div>

        {/* Feature pills */}
        <div className="flex flex-wrap justify-center gap-2 mb-10">
          {[
            { icon: BarChart3, label: "ZOPA-Analyse" },
            { icon: Shield, label: "Constraint-Schutz" },
            { icon: GitBranch, label: "Human-in-the-Loop" },
          ].map(({ icon: Icon, label }) => (
            <div
              key={label}
              className="flex items-center gap-1.5 bg-white border border-gray-200 rounded-full px-3 py-1.5 shadow-sm"
            >
              <Icon className="h-3.5 w-3.5 text-slate-400" />
              <span className="text-xs text-slate-600 font-medium">{label}</span>
            </div>
          ))}
        </div>

        {/* Role cards */}
        <div className="grid sm:grid-cols-2 gap-5 w-full max-w-2xl">
          {/* Retailer */}
          <button
            onClick={() => navigate("/retailer")}
            className="group relative bg-white rounded-2xl border border-gray-200 p-6 text-left
              shadow-sm hover:shadow-md hover:border-[#0070d2] transition-all duration-200
              focus:outline-none focus:ring-2 focus:ring-[#0070d2] focus:ring-offset-2"
          >
            <div className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-xl bg-sky-50 border border-sky-100 group-hover:bg-sky-100 transition-colors">
              <ShoppingCart className="h-6 w-6 text-sky-600" />
            </div>
            <h2 className="text-base font-semibold text-slate-900 mb-1.5">Retailer</h2>
            <p className="text-sm text-slate-500 leading-relaxed mb-5">
              Produkte von Lieferanten beschaffen. Eigene Einkaufslimits setzen — 
              KI-Agenten verhandeln den besten Deal auf Basis Ihrer Marge.
            </p>
            <div className="flex flex-col gap-1.5 text-xs text-slate-500 mb-5">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                Freitext-Anfragen stellen
              </div>
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                Eingehende Angebote prüfen
              </div>
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                Verhandlungen überwachen
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-[#0070d2]">
                Als Retailer einloggen
              </span>
              <ArrowRight className="h-4 w-4 text-[#0070d2] group-hover:translate-x-1 transition-transform" />
            </div>
            {/* Active indicator bar */}
            <div className="absolute bottom-0 left-6 right-6 h-0.5 bg-[#0070d2] rounded-full opacity-0 group-hover:opacity-100 transition-opacity" />
          </button>

          {/* Supplier */}
          <button
            onClick={() => navigate("/supplier")}
            className="group relative bg-white rounded-2xl border border-gray-200 p-6 text-left
              shadow-sm hover:shadow-md hover:border-violet-400 transition-all duration-200
              focus:outline-none focus:ring-2 focus:ring-violet-500 focus:ring-offset-2"
          >
            <div className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-xl bg-violet-50 border border-violet-100 group-hover:bg-violet-100 transition-colors">
              <Truck className="h-6 w-6 text-violet-600" />
            </div>
            <h2 className="text-base font-semibold text-slate-900 mb-1.5">Supplier / Lieferant</h2>
            <p className="text-sm text-slate-500 leading-relaxed mb-5">
              Produkte an Retailer verkaufen. Eigene Preisuntergrenzen definieren — 
              KI-Agenten verhandeln in Ihrem besten Interesse.
            </p>
            <div className="flex flex-col gap-1.5 text-xs text-slate-500 mb-5">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />
                Proaktive Angebote erstellen
              </div>
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />
                Eingehende Anfragen bearbeiten
              </div>
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />
                Laufende Deals verfolgen
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-violet-600">
                Als Supplier einloggen
              </span>
              <ArrowRight className="h-4 w-4 text-violet-600 group-hover:translate-x-1 transition-transform" />
            </div>
            <div className="absolute bottom-0 left-6 right-6 h-0.5 bg-violet-400 rounded-full opacity-0 group-hover:opacity-100 transition-opacity" />
          </button>
        </div>

        <p className="mt-8 text-xs text-slate-400 text-center">
          Beide Parteien verhandeln unabhängig — Constraints bleiben stets vertraulich
        </p>
      </div>
    </div>
  );
}